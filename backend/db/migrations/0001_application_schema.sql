-- =============================================================================
-- 0001_application_schema.sql — DETROIT application schema, multi-user
--
-- WHY THIS FILE EXISTS
--     It creates the `public` tables that persist ProjectState, owned by the
--     authenticated Supabase user and isolated by row-level security. It is the
--     corrected form of the DDL in docs/database.md §3, which §3.0 marks as
--     superseded because it predates the ownership and RLS requirements.
--
-- LAYER
--     Persistence (backend/db/migrations/). Plain SQL, no migration tool:
--     docs/runtime.md §2.1 records that none is chosen and that plain SQL files
--     are the expected answer. Alembic is ORM-adjacent and CLAUDE.md §9A
--     forbids an ORM.
--
-- GROUNDING
--     Every column traces to a field on a Pydantic model, verified against the
--     live classes rather than the prose:
--       public.projects                 -> ProjectState (backend/schemas/state.py)
--       public.hitl_records             -> ProjectState.hitl_history / HitlRecord
--       public.approved_decisions       -> .approved_decisions / ApprovedDecision
--       public.rejected_options         -> .rejected_options / RejectedOption
--       public.external_action_records  -> .mcp_action_history / McpActionRecord
--       public.reference_images         -> .reference_images / ReferenceImageRef
--     No product entity is invented. There is no table here that ProjectState
--     does not already carry.
--
-- WHAT THIS FILE DELIBERATELY DOES NOT DO
--     - No `public.users` table. Identity is `auth.users.id`; there is no
--       profile attribute to store that Supabase does not already hold
--       (docs/database.md §1.3.1).
--     - No LangGraph checkpoint tables. `langgraph-checkpoint-postgres` creates
--       and migrates its own, and hand-writing them would be inventing a schema
--       for a library that owns one (docs/database.md §1.2).
--     - No secret of any kind. No API key, no GitHub token, and no live
--       approval token: those stay in process memory as a SHA-256 digest
--       (docs/database.md §1.4). `external_action_records` records that an
--       action happened, never permission to perform one.
--     - No `node_scratch`. It is `exclude=True` on the model and
--       docs/state.md §2 forbids persisting it.
--
-- SHAPE OF THE DATA
--     ProjectState is NOT shredded into columns. Identity and the fields the
--     API filters or sorts by are columns; structured stage outputs are `jsonb`
--     holding one Pydantic model each; append-only audit lists are child tables
--     (docs/database.md §2). Decomposing all 33 fields would create the second
--     state model docs/state.md §3 forbids.
--
-- TRANSACTIONAL
--     Wrapped in BEGIN/COMMIT. PostgreSQL DDL is transactional, so a failure
--     anywhere leaves the database untouched rather than half-migrated.
--
-- RUN ONCE. There is no IF NOT EXISTS on the tables: re-running must fail
--     loudly rather than silently diverge.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- projects — the ownership root
--
-- owner_id is the whole multi-user design. It is NOT NULL because a project
-- with no owner is invisible under RLS: unreadable, unlistable and undeletable
-- by anyone. ON DELETE CASCADE because a deleted Supabase user must not leave
-- orphaned rows that no policy can ever match.
--
-- The CHECK constraints on current_stage and run_status mirror the WorkflowStage
-- and RunStatus enums. They are a deliberate trade: adding a stage now requires
-- a migration, in exchange for a typo'd stage being impossible to store. The
-- values are the wire format already (backend/schemas/enums.py), so they were
-- never free to change silently.
-- -----------------------------------------------------------------------------
CREATE TABLE public.projects (
    project_id      text PRIMARY KEY
                    CONSTRAINT projects_project_id_format
                    CHECK (project_id ~ '^proj_[0-9a-f]{32}$'),

    -- Supabase Auth is the identity source (docs/database.md §1.3).
    owner_id        uuid        NOT NULL
                    REFERENCES auth.users (id) ON DELETE CASCADE,

    session_id      text        NOT NULL
                    CONSTRAINT projects_session_id_format
                    CHECK (session_id ~ '^sess_[0-9a-f]{32}$'),
    title           text        NOT NULL DEFAULT '',
    user_request    text        NOT NULL DEFAULT '',
    schema_version  integer     NOT NULL,

    current_stage   text        NOT NULL
                    CONSTRAINT projects_current_stage_valid CHECK (current_stage IN (
                        'requirement_analysis', 'planning', 'design',
                        'reference_analysis', 'frontend_architecture', 'build',
                        'preview', 'evaluation', 'targeted_fix', 're_evaluation',
                        'final_validation', 'external_action')),

    run_status      text        NOT NULL
                    CONSTRAINT projects_run_status_valid CHECK (run_status IN (
                        'idle', 'running', 'awaiting_hitl', 'completed',
                        'blocked', 'failed', 'cancelled')),

    iteration_count integer     NOT NULL DEFAULT 0
                    CONSTRAINT projects_iteration_count_non_negative
                    CHECK (iteration_count >= 0),

    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,

    -- Structured stage outputs. Each is the dump of one Pydantic model, and is
    -- NULL exactly when that model is None on the state.
    normalized_requirements jsonb,
    plan                    jsonb,
    design_spec             jsonb,
    reference_analysis      jsonb,
    frontend_architecture   jsonb,
    component_tree          jsonb,
    evaluation_results      jsonb,
    pending_checkpoint      jsonb,          -- NULL unless awaiting HITL
    last_error              jsonb,

    -- Always present on the model (default_factory), therefore NOT NULL here.
    build_status            jsonb NOT NULL,
    preview_status          jsonb NOT NULL,

    -- Replaced wholesale each run rather than appended, so they stay inline
    -- instead of becoming child tables.
    requirement_conflicts   jsonb NOT NULL DEFAULT '[]'::jsonb,
    requirement_gaps        jsonb NOT NULL DEFAULT '[]'::jsonb,
    generated_files         jsonb NOT NULL DEFAULT '[]'::jsonb,
    file_versions           jsonb NOT NULL DEFAULT '{}'::jsonb,

    approved_reference_direction text,
    next_action                  text
);

COMMENT ON TABLE public.projects IS
    'One row per ProjectState. Owned by an auth.users row; RLS-isolated.';
COMMENT ON COLUMN public.projects.owner_id IS
    'auth.users.id. Assigned server-side from the verified JWT, never from a request body.';
COMMENT ON COLUMN public.projects.schema_version IS
    'ProjectState.SCHEMA_VERSION at write time, so a row written by an older model is detectable rather than silently reinterpreted.';

-- -----------------------------------------------------------------------------
-- Append-only audit tables
--
-- None carries its own owner_id. Ownership is inherited through project_id, and
-- a second copy of ownership is a second thing to keep consistent — the two
-- disagreeing would be a silent authorization bug (docs/database.md §1.3.2).
--
-- Every primary key LEADS with project_id, so it also serves the RLS subquery
-- and the cascade lookup. No separate index on project_id is needed.
-- -----------------------------------------------------------------------------

-- hitl_history / HitlRecord. decided_at is part of the key because one
-- checkpoint can legitimately be answered more than once across a revise loop.
CREATE TABLE public.hitl_records (
    project_id    text        NOT NULL
                  REFERENCES public.projects (project_id) ON DELETE CASCADE,
    checkpoint_id text        NOT NULL,
    stage         text        NOT NULL
                  CONSTRAINT hitl_records_stage_valid CHECK (stage IN (
                      'requirement_analysis', 'planning', 'design',
                      'reference_analysis', 'frontend_architecture', 'build',
                      'preview', 'evaluation', 'targeted_fix', 're_evaluation',
                      'final_validation', 'external_action')),
    action        text        NOT NULL
                  CONSTRAINT hitl_records_action_valid
                  CHECK (action IN ('approve', 'select_option', 'custom', 'reject')),
    option_id     text,
    instruction   text,
    decided_at    timestamptz NOT NULL,
    PRIMARY KEY (project_id, checkpoint_id, decided_at),

    -- Mirrors HitlDecisionRequest's model_validator: select_option carries an
    -- option_id and no instruction, custom carries an instruction and no
    -- option_id, approve/reject carry neither. Enforced here too so a record
    -- that could not have been produced by a valid decision cannot be stored.
    CONSTRAINT hitl_records_payload_matches_action CHECK (
        (action = 'select_option' AND option_id IS NOT NULL AND instruction IS NULL)
     OR (action = 'custom'        AND instruction IS NOT NULL AND option_id IS NULL)
     OR (action IN ('approve', 'reject') AND option_id IS NULL AND instruction IS NULL)
    )
);

-- approved_decisions / ApprovedDecision.
--
-- origin is NULLABLE. docs/database.md §3.2 declared it NOT NULL, but the model
-- is `origin: HitlAction | None = None` — a decision can come from an explicit
-- user instruction rather than from a gate. NOT NULL would reject a legitimate
-- row, so the model wins (see the report accompanying this migration).
CREATE TABLE public.approved_decisions (
    project_id  text        NOT NULL
                REFERENCES public.projects (project_id) ON DELETE CASCADE,
    decision_id text        NOT NULL,
    stage       text        NOT NULL
                CONSTRAINT approved_decisions_stage_valid CHECK (stage IN (
                    'requirement_analysis', 'planning', 'design',
                    'reference_analysis', 'frontend_architecture', 'build',
                    'preview', 'evaluation', 'targeted_fix', 're_evaluation',
                    'final_validation', 'external_action')),
    statement   text        NOT NULL,
    origin      text
                CONSTRAINT approved_decisions_origin_valid
                CHECK (origin IS NULL OR origin IN
                       ('approve', 'select_option', 'custom', 'reject')),
    decided_at  timestamptz NOT NULL,
    PRIMARY KEY (project_id, decision_id)
);

-- rejected_options / RejectedOption. Kept so an option is not re-proposed.
CREATE TABLE public.rejected_options (
    project_id    text        NOT NULL
                  REFERENCES public.projects (project_id) ON DELETE CASCADE,
    checkpoint_id text        NOT NULL,
    option_id     text        NOT NULL,
    stage         text        NOT NULL
                  CONSTRAINT rejected_options_stage_valid CHECK (stage IN (
                      'requirement_analysis', 'planning', 'design',
                      'reference_analysis', 'frontend_architecture', 'build',
                      'preview', 'evaluation', 'targeted_fix', 're_evaluation',
                      'final_validation', 'external_action')),
    rejected_at   timestamptz NOT NULL,
    PRIMARY KEY (project_id, checkpoint_id, option_id)
);

-- mcp_action_history / McpActionRecord.
--
-- Exactly the four fields the model has. NO token, NO credential, NO repository
-- URL: docs/mcp.md §6 logs action type, outcome and timestamp, never the
-- credential. action_type is unconstrained text because the model types it as
-- `str`; a CHECK listing the four github_* values would encode a vocabulary the
-- model does not itself enforce.
CREATE TABLE public.external_action_records (
    project_id   text        NOT NULL
                 REFERENCES public.projects (project_id) ON DELETE CASCADE,
    action_id    text        NOT NULL,
    action_type  text        NOT NULL,
    status       text        NOT NULL
                 CONSTRAINT external_action_records_status_valid
                 CHECK (status IN ('succeeded', 'failed')),
    performed_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, action_id)
);

-- reference_images / ReferenceImageRef. Metadata only — the bytes stay on the
-- filesystem for v1 (docs/database.md §4, decision #5). The checksum is here,
-- so the row is enough to detect a changed or missing file.
CREATE TABLE public.reference_images (
    project_id   text        NOT NULL
                 REFERENCES public.projects (project_id) ON DELETE CASCADE,
    image_id     text        NOT NULL
                 CONSTRAINT reference_images_image_id_format
                 CHECK (image_id ~ '^img_[0-9a-f]{32}$'),
    content_type text        NOT NULL,       -- confirmed by magic bytes at upload
    size_bytes   integer     NOT NULL
                 CONSTRAINT reference_images_size_non_negative CHECK (size_bytes >= 0),
    sha256       text        NOT NULL,
    uploaded_at  timestamptz NOT NULL,
    PRIMARY KEY (project_id, image_id)
);

-- -----------------------------------------------------------------------------
-- Indexes
--
-- Only what a real query needs. The listing query is
--   SELECT ... FROM projects WHERE owner_id = $1 ORDER BY updated_at DESC
-- so a composite leading with owner_id serves both the filter and the sort; a
-- standalone owner_id index would be redundant with it.
--
-- There is deliberately no index on the child tables: each primary key already
-- leads with project_id, which is what the RLS subquery and the cascade use.
-- -----------------------------------------------------------------------------
CREATE INDEX projects_owner_updated_idx
    ON public.projects (owner_id, updated_at DESC);

CREATE INDEX projects_owner_run_status_idx
    ON public.projects (owner_id, run_status);

-- -----------------------------------------------------------------------------
-- Row-level security
--
-- Enabled on EVERY table. A policy on projects alone would not protect the
-- children: PostgreSQL evaluates policies per table, and a child with RLS off
-- is readable by anyone holding a key (docs/database.md §1.3.3).
--
-- RLS defaults to deny. A table with RLS enabled and no matching policy returns
-- zero rows and rejects writes, which is how the append-only guarantee below is
-- enforced: the audit tables simply have no UPDATE or DELETE policy.
--
-- FORCE ROW LEVEL SECURITY is applied so the table OWNER is also subject to the
-- policies. Without it the owning role is exempt, which would quietly reopen
-- the hole this migration exists to close. It does NOT constrain a role with
-- BYPASSRLS — see the note at the end of this file.
-- -----------------------------------------------------------------------------
ALTER TABLE public.projects                ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.projects                FORCE  ROW LEVEL SECURITY;
ALTER TABLE public.hitl_records            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hitl_records            FORCE  ROW LEVEL SECURITY;
ALTER TABLE public.approved_decisions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.approved_decisions      FORCE  ROW LEVEL SECURITY;
ALTER TABLE public.rejected_options        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rejected_options        FORCE  ROW LEVEL SECURITY;
ALTER TABLE public.external_action_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.external_action_records FORCE  ROW LEVEL SECURITY;
ALTER TABLE public.reference_images        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reference_images        FORCE  ROW LEVEL SECURITY;

-- projects: full CRUD, scoped to the caller.
--
-- `(SELECT auth.uid())` rather than a bare call: wrapping it lets the planner
-- evaluate it once per statement as an InitPlan instead of once per row.
--
-- UPDATE carries BOTH USING and WITH CHECK. USING decides which rows may be
-- updated; WITH CHECK decides what they may become. Without WITH CHECK a user
-- could reassign owner_id and hand their project to someone else — or take
-- one.
CREATE POLICY projects_select_own ON public.projects
    FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));

CREATE POLICY projects_insert_own ON public.projects
    FOR INSERT TO authenticated
    WITH CHECK (owner_id = (SELECT auth.uid()));

CREATE POLICY projects_update_own ON public.projects
    FOR UPDATE TO authenticated
    USING      (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid()));

CREATE POLICY projects_delete_own ON public.projects
    FOR DELETE TO authenticated
    USING (owner_id = (SELECT auth.uid()));

-- Child tables: SELECT and INSERT only, each gated on owning the parent.
--
-- The absence of UPDATE and DELETE policies is the append-only enforcement.
-- Deleting a project still removes its children, because ON DELETE CASCADE is a
-- referential action performed by the system and is not filtered by the child
-- table's policies.
CREATE POLICY hitl_records_select_own ON public.hitl_records
    FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = hitl_records.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY hitl_records_insert_own ON public.hitl_records
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = hitl_records.project_id
                          AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY approved_decisions_select_own ON public.approved_decisions
    FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = approved_decisions.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY approved_decisions_insert_own ON public.approved_decisions
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = approved_decisions.project_id
                          AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY rejected_options_select_own ON public.rejected_options
    FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = rejected_options.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY rejected_options_insert_own ON public.rejected_options
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = rejected_options.project_id
                          AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY external_action_records_select_own ON public.external_action_records
    FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = external_action_records.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY external_action_records_insert_own ON public.external_action_records
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = external_action_records.project_id
                          AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY reference_images_select_own ON public.reference_images
    FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = reference_images.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY reference_images_insert_own ON public.reference_images
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = reference_images.project_id
                          AND p.owner_id = (SELECT auth.uid())));

-- -----------------------------------------------------------------------------
-- Privileges
--
-- A policy filters rows; a GRANT decides whether the role may touch the table at
-- all. Both are required — RLS without a GRANT denies everything, and a GRANT
-- without RLS exposes everything.
--
-- THE REVOKE BELOW IS NOT TIDY-UP. IT CLOSES A HOLE.
--
-- Supabase ships `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES
-- TO anon, authenticated, service_role`. Every table created above therefore
-- arrives with the FULL privilege set already granted to `authenticated` —
-- `arwdDxtm`, which includes UPDATE, DELETE, TRUNCATE, REFERENCES and TRIGGER.
--
-- TRUNCATE is the dangerous one: **row-level security does not apply to
-- TRUNCATE.** Policies filter rows for SELECT/INSERT/UPDATE/DELETE and are
-- simply not consulted for it. Left as shipped, any signed-in user could run
--     TRUNCATE public.projects CASCADE;
-- and destroy every other user's data, with all twelve policies above intact
-- and doing exactly what they were written to do. Verified against this
-- database before writing this comment.
--
-- So privileges are reset to nothing and re-granted explicitly. After this,
-- the GRANT and the policy set agree, and each is a independent layer: a
-- mistakenly added UPDATE policy on an append-only table still cannot be used,
-- because the privilege is absent.
--
-- service_role is left as Supabase configured it. It holds BYPASSRLS regardless
-- (docs/database.md §1.3.4), so revoking its table privileges would buy nothing
-- while risking the platform tooling that expects it.
-- -----------------------------------------------------------------------------
REVOKE ALL ON public.projects                FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.hitl_records            FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.approved_decisions      FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.rejected_options        FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.external_action_records FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.reference_images        FROM PUBLIC, anon, authenticated;

-- anon is granted nothing at all: there is no anonymous path in this product.

GRANT SELECT, INSERT, UPDATE, DELETE ON public.projects TO authenticated;

-- Append-only, enforced twice over: no UPDATE/DELETE policy exists, and no
-- UPDATE/DELETE privilege is granted. Neither TRUNCATE nor REFERENCES nor
-- TRIGGER is granted on any table.
GRANT SELECT, INSERT ON public.hitl_records            TO authenticated;
GRANT SELECT, INSERT ON public.approved_decisions      TO authenticated;
GRANT SELECT, INSERT ON public.rejected_options        TO authenticated;
GRANT SELECT, INSERT ON public.external_action_records TO authenticated;
GRANT SELECT, INSERT ON public.reference_images        TO authenticated;

COMMIT;

-- =============================================================================
-- TWO THINGS THIS MIGRATION DOES NOT SETTLE
--
-- 1. THE BACKEND'S CONNECTION ROLE IS STILL OPEN (docs/database.md §5.2 #7).
--    Everything above is correct and complete for a caller arriving as
--    `authenticated`. The backend currently connects as `postgres`, which has
--    BYPASSRLS — so for the backend's own path these policies are inert until
--    #7 is resolved. Resolving it toward a dedicated NOBYPASSRLS role adds
--    GRANT statements in a later migration; it does NOT change a single policy
--    here.
--
-- 2. NO updated_at TRIGGER, deliberately. ProjectState owns that timestamp and
--    sets it in Python. A BEFORE UPDATE trigger would overwrite the value the
--    state carries, so the row and the checkpoint would disagree about when the
--    project last moved — and docs/state.md §5.1 makes the checkpoint
--    authoritative, which would make the column quietly wrong.
-- =============================================================================
