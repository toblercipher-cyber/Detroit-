/**
 * GitHub integration panel for the builder workspace.
 *
 * Why this file exists:
 * `/chat`'s right rail previously listed MCP servers with a "No status" dot and
 * a note that no endpoint existed. One now does, so this replaces the honest
 * placeholder with an honest working panel: connect a repository, see what a
 * a push would change, approve it, and read the result.
 *
 * Layer: frontend presentation (workspace component). It talks to the backend
 * only through `@/lib/api` and holds no transport knowledge of its own.
 *
 * ---------------------------------------------------------------------------
 * EVERY STATE IS A REAL STATE, INCLUDING THE BAD ONES
 *
 *   unconfigured  the server has no credential — say so, offer nothing
 *   loading       a request is in flight
 *   disconnected  usable, but this project has no repository yet
 *   connected     a repository is selected
 *   preview       a plan is on screen awaiting approval
 *   success       what actually happened, with a link where there is one
 *   error         the backend's own message, never one invented here
 *
 * The panel never shows a "Connected" pill it cannot stand behind: the status
 * comes from a backend call that actually tries GitHub, not from a flag.
 *
 * TWO-PHASE BY CONSTRUCTION
 * A push is impossible to trigger in one click. `previewPush` returns a plan
 * plus a single-use token, the user reads the file list, and only then does
 * `executePush` run. That mirrors the server-side gate rather than decorating
 * it — a UI that skipped the preview simply would not have a token.
 *
 * PUSH IS THE ONLY OPERATION. Pull/import and pull-request creation were
 * removed as out of scope; the workspace does not import a repository and
 * DETROIT never creates one.
 * ---------------------------------------------------------------------------
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowUpFromLine,
  Check,
  Github,
  Loader2,
  RefreshCw,
  Unlink,
} from "lucide-react";

import {
  disconnectRepository,
  executePush,
  getGitHubLink,
  getGitHubStatus,
  isApiError,
  listRepositories,
  previewPush,
  selectRepository,
  type ApiError,
  type GitHubLink,
  type GitHubStatus,
  type RepositorySummary,
  type SyncChange,
  type SyncPlan,
  type SyncResult,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Tone per change kind. Conflict reads as a warning, never as an error. */
const KIND_TONE: Record<SyncChange["kind"], string> = {
  add: "text-emerald-400",
  update: "text-ember",
  skip: "text-muted-foreground/60",
};

const KIND_LABEL: Record<SyncChange["kind"], string> = {
  add: "add",
  update: "update",
  skip: "skipped",
};

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground">
      {children}
    </p>
  );
}

function Row({ change }: { change: SyncChange }) {
  return (
    <li className="flex items-baseline gap-2 py-1">
      <span
        className={cn(
          "w-16 shrink-0 font-mono text-[9px] uppercase tracking-[0.12em]",
          KIND_TONE[change.kind],
        )}
      >
        {KIND_LABEL[change.kind]}
      </span>
      <span className="min-w-0 flex-1 truncate text-[12px] text-foreground/85">
        {change.path}
      </span>
      {change.reason && (
        <span
          title={change.reason}
          className="shrink-0 truncate text-[10px] text-muted-foreground"
        >
          {change.reason}
        </span>
      )}
    </li>
  );
}

export function GitHubPanel({ projectId }: { projectId: string | null }) {
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [link, setLink] = useState<GitHubLink | null>(null);
  const [repos, setRepos] = useState<RepositorySummary[] | null>(null);
  const [plan, setPlan] = useState<SyncPlan | null>(null);
  const [result, setResult] = useState<SyncResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  /* The primary way in: the user pastes their repository's URL. The picker
     below is a convenience for people who would rather not go and copy one. */
  const [url, setUrl] = useState("");
  const [showPicker, setShowPicker] = useState(false);

  const fail = useCallback((cause: unknown) => {
    if (isApiError(cause)) setError(cause);
  }, []);

  /* Server capability. One call, because it costs a real GitHub request. */
  useEffect(() => {
    let alive = true;
    getGitHubStatus()
      .then((s) => alive && setStatus(s))
      .catch((c) => alive && fail(c));
    return () => {
      alive = false;
    };
  }, [fail]);

  /* The project's own link, re-read whenever the active project changes. */
  useEffect(() => {
    if (!projectId) {
      setLink(null);
      return;
    }
    let alive = true;
    getGitHubLink(projectId)
      .then((l) => alive && setLink(l))
      .catch((c) => alive && fail(c));
    return () => {
      alive = false;
    };
  }, [projectId, fail]);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (cause) {
      fail(cause);
    } finally {
      setBusy(null);
    }
  }

  const loadRepos = () =>
    run("repos", async () => {
      const { repositories } = await listRepositories();
      setRepos(repositories);
    });

  /* `value` may be a full GitHub URL, a clone string, or `owner/name`. The
     backend normalises and validates it, and verifies BOTH that the
     repository exists and that the credential can push to it, before storing
     anything. Nothing is parsed here — a second parser would be a second set
     of rules to keep in step. */
  const connect = (value: string) =>
    run("connect", async () => {
      setLink(await selectRepository(projectId!, { repository: value.trim() }));
      setRepos(null);
      setShowPicker(false);
      setUrl("");
      setPlan(null);
      setResult(null);
    });

  const disconnect = () =>
    run("disconnect", async () => {
      await disconnectRepository(projectId!);
      setLink({
        connected: false,
        repository: null,
        default_branch: null,
        branch: null,
        last_pushed_at: null,
        last_commit_sha: null,
      });
      setPlan(null);
      setResult(null);
    });

  /* Push is the only sync this workspace offers. */
  const preview = () =>
    run("push", async () => {
      setResult(null);
      setPlan(await previewPush(projectId!));
    });

  const apply = () =>
    run("apply", async () => {
      if (!plan) return;
      /* No pull request is opened and nothing is imported: pushing the
         generated project is the whole operation. */
      setResult(
        await executePush(projectId!, {
          plan_id: plan.plan_id,
          approval_token: plan.approval_token,
        }),
      );
      /* The token is now spent. Dropping the plan stops the button offering a
         second run that the server would refuse with 410. */
      setPlan(null);
      setLink(await getGitHubLink(projectId!));
    });

  /* ---- unconfigured: offer nothing, and say why ---- */
  if (status && !status.configured) {
    return (
      <section>
        <Eyebrow>GitHub</Eyebrow>
        <div className="mt-3 rounded-lg border border-border bg-card/60 px-3.5 py-3">
          <p className="flex items-center gap-2 text-[13px] text-foreground/85">
            <Github size={14} className="shrink-0 text-muted-foreground" />
            Not connected
          </p>
          <p className="mt-1.5 text-[12px] leading-relaxed text-muted-foreground">
            {status.reason ||
              "No GitHub credential is configured on this server."}
          </p>
        </div>
      </section>
    );
  }

  if (!status) {
    return (
      <section>
        <Eyebrow>GitHub</Eyebrow>
        <p className="mt-3 flex items-center gap-2 text-[12px] text-muted-foreground">
          <Loader2 size={13} className="animate-spin" />
          Checking GitHub…
        </p>
      </section>
    );
  }

  return (
    <section>
      <div className="flex items-center justify-between gap-2">
        <Eyebrow>GitHub</Eyebrow>
        {status.account && (
          <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-emerald-400">
            {status.account}
          </span>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="mt-3 flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/[0.07] px-3 py-2"
        >
          <AlertTriangle size={13} className="mt-0.5 shrink-0 text-rose-400" />
          <div className="min-w-0">
            <p className="text-[12px] leading-relaxed text-foreground/90">
              {error.message}
            </p>
            <p className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
              {error.code}
            </p>
          </div>
        </div>
      )}

      {!projectId && (
        <p className="mt-3 text-[12px] text-muted-foreground">
          Open or start a project to connect a repository.
        </p>
      )}

      {/* ---- disconnected: paste a repository URL ---- */}
      {projectId && link && !link.connected && (
        <div className="mt-3 space-y-2">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (url.trim()) connect(url);
            }}
          >
            <label
              htmlFor="gh-url"
              className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground"
            >
              Repository URL
            </label>
            <input
              id="gh-url"
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/you/your-repo"
              spellCheck={false}
              autoComplete="off"
              className="mt-1 w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-[12px] text-foreground outline-none placeholder:text-muted-foreground/60 focus:border-ember/60"
            />
            <Button
              type="submit"
              className="mt-2 h-8 w-full text-[12px]"
              disabled={!url.trim() || busy === "connect"}
            >
              {busy === "connect" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Github size={13} />
              )}
              Connect repository
            </Button>
          </form>

          <p className="text-[11px] leading-relaxed text-muted-foreground">
            DETROIT checks that the repository exists and that it can push
            before connecting. It never creates a repository.
          </p>

          {/* Secondary: for someone who would rather not go and copy a URL. */}
          {!showPicker && (
            <button
              type="button"
              onClick={() => {
                setShowPicker(true);
                void loadRepos();
              }}
              className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-foreground"
            >
              or choose from your repositories
            </button>
          )}

          {showPicker && busy === "repos" && (
            <p className="flex items-center gap-2 text-[12px] text-muted-foreground">
              <Loader2 size={12} className="animate-spin" /> Loading…
            </p>
          )}
          {showPicker && repos && repos.length === 0 && (
            <p className="text-[12px] text-muted-foreground">
              The configured credential can see no repositories.
            </p>
          )}
          {showPicker && repos && repos.length > 0 && (
            <ul className="max-h-48 space-y-0.5 overflow-y-auto">
              {repos.map((r) => (
                <li key={r.full_name}>
                  <button
                    type="button"
                    disabled={!r.can_push || busy === "connect"}
                    onClick={() => connect(r.full_name)}
                    title={
                      r.can_push
                        ? `Connect ${r.full_name}`
                        : "The credential cannot push to this repository"
                    }
                    className={cn(
                      "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px]",
                      r.can_push
                        ? "text-foreground/85 hover:bg-card hover:text-ember"
                        : "cursor-not-allowed text-muted-foreground/50",
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate">{r.full_name}</span>
                    {r.private && (
                      <span className="shrink-0 font-mono text-[8px] uppercase tracking-[0.12em] text-muted-foreground">
                        private
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* ---- connected ---- */}
      {projectId && link?.connected && (
        <div className="mt-3 space-y-3">
          <div className="rounded-lg border border-border bg-card/60 px-3 py-2.5">
            <p className="flex items-center gap-2 truncate text-[13px] text-foreground">
              <Github size={13} className="shrink-0 text-muted-foreground" />
              {link.repository}
            </p>
            <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
              branch {link.branch}
              {link.last_commit_sha
                ? ` · ${link.last_commit_sha.slice(0, 7)}`
                : ""}
            </p>
          </div>

          <div className="flex gap-2">
            <Button
              variant="outline"
              className="h-8 flex-1 text-[12px]"
              onClick={() => preview()}
              disabled={!!busy}
            >
              {busy === "push" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <ArrowUpFromLine size={13} />
              )}
              Push
            </Button>

          </div>

          {/* ---- preview awaiting approval ---- */}
          {plan && (
            <div className="rounded-lg border border-ember/40 bg-ember/[0.05] px-3 py-2.5">
              <p className="text-[12px] text-foreground/90">
                Push {plan.repository}
                <span className="text-muted-foreground"> · {plan.branch}</span>
              </p>
              <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                {plan.added} add · {plan.updated} update
                {plan.skipped > 0 ? ` · ${plan.skipped} skipped` : ""}
              </p>

              {plan.changes.length > 0 && (
                <ul className="mt-2 max-h-48 divide-y divide-border/50 overflow-y-auto">
                  {plan.changes.map((c) => (
                    <Row key={`${c.kind}-${c.path}`} change={c} />
                  ))}
                </ul>
              )}

              <div className="mt-2.5 flex gap-2">
                <Button
                  className="h-7 flex-1 text-[11px]"
                  onClick={apply}
                  disabled={busy === "apply" || !(plan.added || plan.updated)}
                >
                  {busy === "apply" ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <Check size={12} />
                  )}
                  Approve
                </Button>
                <Button
                  variant="outline"
                  className="h-7 flex-1 text-[11px]"
                  onClick={() => setPlan(null)}
                  disabled={busy === "apply"}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {/* ---- success ---- */}
          {result && (
            <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/[0.06] px-3 py-2.5">
              <p className="flex items-start gap-2 text-[12px] leading-relaxed text-foreground/90">
                <Check size={13} className="mt-0.5 shrink-0 text-emerald-400" />
                {result.detail}
              </p>
            </div>
          )}

          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => preview()}
              disabled={!!busy}
              className="inline-flex items-center gap-1 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:text-foreground"
            >
              <RefreshCw size={10} /> Refresh
            </button>
            <button
              type="button"
              onClick={disconnect}
              disabled={!!busy}
              className="ml-auto inline-flex items-center gap-1 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:text-rose-400"
            >
              <Unlink size={10} /> Disconnect
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
