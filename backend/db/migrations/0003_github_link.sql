-- =============================================================================
-- 0003_github_link.sql — the repository a project is connected to.
--
-- WHY THIS MIGRATION EXISTS
--   ProjectState gained a `github` field (backend/schemas/state.py:GitHubLink)
--   recording which repository a project pushes to and pulls from. The
--   PostgreSQL repository maps EXPLICIT columns rather than serialising the
--   whole model, so a new field is invisible to the database until it has a
--   column. Without this migration the selection would appear to work and then
--   vanish on the next load.
--
-- WHY jsonb AND NOT SEPARATE COLUMNS
--   The link is a small, cohesive value object that is always read and written
--   as a unit, exactly like `plan` and `design_spec` beside it. Splitting it
--   into five columns would mean five more migrations the next time it gains a
--   field, for no query we intend to run. Nothing filters or joins on its
--   interior.
--
-- WHY NO CREDENTIAL COLUMN
--   Deliberately absent, and it must stay absent. The GitHub token is server
--   configuration (`GITHUB_TOKEN`), not per-project data. Storing a credential
--   here would put it inside a row that RLS protects but that backups, logs
--   and a future admin query would all carry (docs/security.md 2).
--
-- OWNERSHIP AND RLS
--   None of the existing policies need changing. They are row-level on
--   public.projects and this adds a column to that table, so the column
--   inherits every policy already in force: a user reads and writes this value
--   only on rows they own (docs/database.md).
--
-- IDEMPOTENT. Safe to re-run; it alters nothing that already exists.
-- =============================================================================

BEGIN;

ALTER TABLE public.projects
    ADD COLUMN IF NOT EXISTS github jsonb;

COMMENT ON COLUMN public.projects.github IS
    'GitHubLink: the connected repository, branch and last-sync timestamps. '
    'Never a credential — the token is server configuration.';

COMMIT;
