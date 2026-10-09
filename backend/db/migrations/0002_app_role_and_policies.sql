-- =============================================================================
-- 0002_app_role_and_policies.sql — least-privilege application role
--
-- WHY THIS MIGRATION EXISTS
--     0001 enabled RLS and wrote 14 correct policies. They do nothing for the
--     backend, because it connects as `postgres`, which carries BYPASSRLS.
--     Verified against this database, not assumed:
--
--         rolname    rolsuper  rolbypassrls
--         postgres     false       true
--
--     So isolation currently rests entirely on application code getting every
--     WHERE clause right, with no database backstop. This migration gives the
--     backend a role that policies actually apply to.
--
-- WHAT CHANGES
--     - a new `detroit_app` role: LOGIN, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE
--     - the 14 policies are repointed from `authenticated` to `detroit_app`
--     - `anon` and `authenticated` lose every table privilege
--
-- WHY POLICIES MOVE OFF `authenticated`
--     `TO authenticated` is the Supabase default because the browser normally
--     talks to PostgREST. DETROIT decided the opposite: the frontend uses
--     Supabase Auth for identity only, and every data operation goes through
--     FastAPI. Nothing with a publishable key should reach these tables at all.
--
--     That also removes a moving part. With `TO authenticated` the backend
--     would have to `SET LOCAL ROLE authenticated` on every transaction, and
--     forgetting it would leave the connection as its own role. Policies on
--     `detroit_app` mean the connected role is already the right one, and the
--     only per-transaction step is the claim.
--
-- HOW IDENTITY REACHES A POLICY
--     `auth.uid()` reads the `request.jwt.claims` GUC; it does not care which
--     role is connected. The backend sets that GUC transaction-locally from a
--     VERIFIED token. With no claim, `auth.uid()` is NULL, every policy
--     evaluates false, and queries return zero rows — it fails closed.
--     Verified on this database before this file was written.
--
-- WHAT THIS MIGRATION DOES NOT CONTAIN
--     **The password.** A credential must never live in a file that is read,
--     diffed or committed. The role is created able to log in but without one;
--     the apply step sets it separately from a generated value that is written
--     only to `.env` (docs/security.md §2).
--
-- SAFETY
--     Additive and non-destructive. It creates no table, drops no table, and
--     touches no row. The only removals are privilege grants to `anon` and
--     `authenticated`, which nothing currently uses — the backend is still on
--     `postgres` at the moment this runs, so applying it cannot lock the
--     application out.
--
-- RUN ONCE, after 0001. Wrapped in BEGIN/COMMIT.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- The application role
--
-- NOBYPASSRLS is the whole point and is stated explicitly rather than relied on
-- as a default, so a reader can see the intent.
--
-- NOCREATEDB / NOCREATEROLE: this role runs application queries. It never
-- migrates, never creates a table, and never manages another role. Schema work
-- stays on the `postgres` connection (see the two-pool note at the end).
-- -----------------------------------------------------------------------------
CREATE ROLE detroit_app WITH
    LOGIN
    NOBYPASSRLS
    NOCREATEDB
    NOCREATEROLE
    NOSUPERUSER
    NOREPLICATION
    INHERIT;

COMMENT ON ROLE detroit_app IS
    'FastAPI application role. RLS applies to it. Password set out of band; '
    'never granted on the langgraph schema.';

-- It needs to see the schema before it can see a table in it.
GRANT USAGE ON SCHEMA public TO detroit_app;

-- -----------------------------------------------------------------------------
-- Privileges — the same shape 0001 gave `authenticated`
--
-- projects is full CRUD; the audit tables are append-only, enforced twice: no
-- UPDATE/DELETE privilege here, and no UPDATE/DELETE policy below. TRUNCATE is
-- never granted, because RLS does not apply to TRUNCATE — that was the hole
-- 0001 closed and it must not reopen here.
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON public.projects TO detroit_app;
GRANT SELECT, INSERT ON public.hitl_records            TO detroit_app;
GRANT SELECT, INSERT ON public.approved_decisions      TO detroit_app;
GRANT SELECT, INSERT ON public.rejected_options        TO detroit_app;
GRANT SELECT, INSERT ON public.external_action_records TO detroit_app;
GRANT SELECT, INSERT ON public.reference_images        TO detroit_app;

-- -----------------------------------------------------------------------------
-- The browser roles lose everything
--
-- Application data goes through FastAPI. A publishable key in a bundle should
-- buy exactly nothing against these tables.
-- -----------------------------------------------------------------------------
REVOKE ALL ON public.projects                FROM anon, authenticated;
REVOKE ALL ON public.hitl_records            FROM anon, authenticated;
REVOKE ALL ON public.approved_decisions      FROM anon, authenticated;
REVOKE ALL ON public.rejected_options        FROM anon, authenticated;
REVOKE ALL ON public.external_action_records FROM anon, authenticated;
REVOKE ALL ON public.reference_images        FROM anon, authenticated;

-- -----------------------------------------------------------------------------
-- The checkpoint schema is not the application's to read
--
-- A checkpoint row holds the serialized ProjectState, including the raw prompt
-- the API deliberately redacts. detroit_app is never granted USAGE here, so
-- even a compromised application query cannot reach another user's checkpoint
-- rows. Only the privileged checkpointer pool touches this schema.
-- -----------------------------------------------------------------------------
REVOKE ALL ON SCHEMA langgraph FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA langgraph FROM PUBLIC, anon, authenticated;

-- -----------------------------------------------------------------------------
-- Repoint the policies
--
-- Dropped and recreated rather than altered: ALTER POLICY can change the role
-- list, but writing them out keeps each policy's full definition visible in one
-- place instead of split across two migrations.
--
-- `(SELECT auth.uid())` stays wrapped so the planner evaluates it once per
-- statement rather than once per row.
-- -----------------------------------------------------------------------------
DROP POLICY projects_select_own                ON public.projects;
DROP POLICY projects_insert_own                ON public.projects;
DROP POLICY projects_update_own                ON public.projects;
DROP POLICY projects_delete_own                ON public.projects;
DROP POLICY hitl_records_select_own            ON public.hitl_records;
DROP POLICY hitl_records_insert_own            ON public.hitl_records;
DROP POLICY approved_decisions_select_own      ON public.approved_decisions;
DROP POLICY approved_decisions_insert_own      ON public.approved_decisions;
DROP POLICY rejected_options_select_own        ON public.rejected_options;
DROP POLICY rejected_options_insert_own        ON public.rejected_options;
DROP POLICY external_action_records_select_own ON public.external_action_records;
DROP POLICY external_action_records_insert_own ON public.external_action_records;
DROP POLICY reference_images_select_own        ON public.reference_images;
DROP POLICY reference_images_insert_own        ON public.reference_images;

CREATE POLICY projects_select_own ON public.projects
    FOR SELECT TO detroit_app
    USING (owner_id = (SELECT auth.uid()));

CREATE POLICY projects_insert_own ON public.projects
    FOR INSERT TO detroit_app
    WITH CHECK (owner_id = (SELECT auth.uid()));

-- USING decides which rows may be updated; WITH CHECK decides what they may
-- become. Without the second, a caller could reassign owner_id and give away —
-- or take — a project.
CREATE POLICY projects_update_own ON public.projects
    FOR UPDATE TO detroit_app
    USING      (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid()));

CREATE POLICY projects_delete_own ON public.projects
    FOR DELETE TO detroit_app
    USING (owner_id = (SELECT auth.uid()));

CREATE POLICY hitl_records_select_own ON public.hitl_records
    FOR SELECT TO detroit_app
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = hitl_records.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY hitl_records_insert_own ON public.hitl_records
    FOR INSERT TO detroit_app
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = hitl_records.project_id
                          AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY approved_decisions_select_own ON public.approved_decisions
    FOR SELECT TO detroit_app
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = approved_decisions.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY approved_decisions_insert_own ON public.approved_decisions
    FOR INSERT TO detroit_app
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = approved_decisions.project_id
                          AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY rejected_options_select_own ON public.rejected_options
    FOR SELECT TO detroit_app
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = rejected_options.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY rejected_options_insert_own ON public.rejected_options
    FOR INSERT TO detroit_app
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = rejected_options.project_id
                          AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY external_action_records_select_own ON public.external_action_records
    FOR SELECT TO detroit_app
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = external_action_records.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY external_action_records_insert_own ON public.external_action_records
    FOR INSERT TO detroit_app
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = external_action_records.project_id
                          AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY reference_images_select_own ON public.reference_images
    FOR SELECT TO detroit_app
    USING (EXISTS (SELECT 1 FROM public.projects p
                   WHERE p.project_id = reference_images.project_id
                     AND p.owner_id = (SELECT auth.uid())));

CREATE POLICY reference_images_insert_own ON public.reference_images
    FOR INSERT TO detroit_app
    WITH CHECK (EXISTS (SELECT 1 FROM public.projects p
                        WHERE p.project_id = reference_images.project_id
                          AND p.owner_id = (SELECT auth.uid())));

COMMIT;

-- =============================================================================
-- THE TWO POOLS, AND THE ONE RISK THAT REMAINS
--
-- app pool          detroit_app   the six public tables. RLS applies. Claims
--                                 set per transaction, so it CANNOT run with
--                                 autocommit: SET LOCAL needs a transaction to
--                                 be local to.
-- checkpointer pool postgres      the langgraph schema only. Needs DDL for the
--                                 library's setup() and autocommit for it.
--
-- RESIDUAL RISK, stated plainly: the checkpointer pool keeps BYPASSRLS, and
-- LangGraph's tables have no ownership column — they are not ours to design.
-- Anything able to call the graph with a thread_id could therefore reach
-- another user's checkpoint. The database cannot prevent that; only the service
-- can, by refusing to touch the graph until an owner-scoped repository load has
-- succeeded for that project_id. That ordering is the control, and it is the
-- thing to protect in review.
-- =============================================================================
