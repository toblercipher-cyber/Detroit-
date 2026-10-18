/**
 * Live agent activity, from real AG-UI events.
 *
 * Why this file exists:
 * The workspace could previously show one stage name from a 1.5s poll. The
 * backend now streams what the graph is actually doing, and this renders it as
 * a timeline the user can follow.
 *
 * Layer: frontend presentation. It receives entries; it does not fetch.
 *
 * ---------------------------------------------------------------------------
 * EVERY ROW IS SOMETHING THAT HAPPENED
 *
 * Entries come from graph nodes, tool calls, HITL interrupts, quality results
 * and build outcomes. There is no timer, no synthetic progress bar, and no
 * percentage — the graph does not produce one, so inventing a number would be
 * a lie with a progress bar attached.
 *
 * It also shows no chain-of-thought. The model's reasoning is not exposed by
 * the backend and is not guessed at here: rows say what the system is DOING,
 * which is observable, not what a model is thinking, which is not.
 * ---------------------------------------------------------------------------
 */

import { CheckCircle2, CircleDashed, Loader2, TriangleAlert } from "lucide-react";

import type { ActivityEntry, ConnectionState, QualityVerdict } from "@/lib/agui";
import { cn } from "@/lib/utils";

/** Semantic tone -> the project's ember-on-near-black palette.
 *
 * Violet and cyan were used here first and Impeccable flagged both as the
 * recognisable tell of AI-generated UI. It was right: they were picked for
 * contrast, not from the approved system. These are the five accents the
 * project already uses — ember, amber, emerald, rose and neutral. */
const TONE: Record<string, string> = {
  running: "text-foreground/80 border-border",
  waiting: "text-amber-400 border-amber-400/40",
  quality: "text-ember border-ember/40",
  fixing: "text-ember/80 border-ember/30",
  building: "text-foreground/70 border-foreground/20",
  tool: "text-emerald-400 border-emerald-400/40",
  error: "text-rose-400 border-rose-500/50",
};

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: "Connecting to the build",
  open: "Live",
  closed: "Stream ended",
  error: "Stream unavailable",
  unsupported: "Live updates unavailable",
};

function Icon({ status }: { status: ActivityEntry["status"] }) {
  if (status === "running")
    return <Loader2 size={12} className="animate-spin" aria-hidden />;
  if (status === "failed") return <TriangleAlert size={12} aria-hidden />;
  return <CheckCircle2 size={12} aria-hidden />;
}

export function ActivityTimeline({
  entries,
  connection,
  connectionDetail,
  quality,
}: {
  entries: ActivityEntry[];
  connection: ConnectionState;
  connectionDetail?: string;
  quality: QualityVerdict | null;
}) {
  return (
    <section aria-label="Build activity">
      <div className="flex items-center justify-between gap-2">
        <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground">
          Activity
        </p>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.12em]",
            connection === "open"
              ? "text-emerald-400"
              : connection === "error"
                ? "text-rose-400"
                : "text-muted-foreground",
          )}
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              connection === "open"
                ? "animate-pulse bg-emerald-400"
                : connection === "error"
                  ? "bg-rose-400"
                  : "bg-muted-foreground/50",
            )}
          />
          {CONNECTION_LABEL[connection]}
        </span>
      </div>

      {connection === "error" && connectionDetail && (
        <p className="mt-2 rounded-md border border-rose-500/40 bg-rose-500/[0.07] px-2.5 py-2 text-[11px] leading-relaxed text-foreground/85">
          {connectionDetail} The build keeps running on the server; reload to
          reconnect.
        </p>
      )}

      {entries.length === 0 ? (
        <p className="mt-3 flex items-center gap-2 text-[12px] text-muted-foreground">
          <CircleDashed size={12} />
          {connection === "open"
            ? "Waiting for the first step…"
            : "No activity yet."}
        </p>
      ) : (
        <ol className="mt-3 space-y-0">
          {entries.map((entry, index) => {
            const tone = TONE[entry.tone] ?? TONE.running;
            const isLast = index === entries.length - 1;
            return (
              <li key={entry.id} className="flex gap-2.5">
                {/* Rail: a dot per step, joined except at the end. */}
                <div className="flex flex-col items-center">
                  <span
                    className={cn(
                      "mt-1 flex size-4 shrink-0 items-center justify-center rounded-full border bg-background",
                      tone,
                    )}
                  >
                    <Icon status={entry.status} />
                  </span>
                  {!isLast && <span className="w-px flex-1 bg-border" />}
                </div>
                <div className={cn("min-w-0 flex-1", isLast ? "pb-0" : "pb-2.5")}>
                  <p
                    className={cn(
                      "truncate text-[12px] leading-tight",
                      entry.status === "failed"
                        ? "text-rose-400"
                        : "text-foreground/90",
                    )}
                  >
                    {entry.label}
                  </p>
                  {entry.detail && (
                    <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
                      {entry.detail}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {/* The quality gate is reported exactly as recorded. A blocked gate is
          never rendered as a pass (CLAUDE.md §15). */}
      {quality && (
        <div
          className={cn(
            "mt-3 rounded-md border px-2.5 py-2",
            quality.gateStatus === "passed"
              ? "border-emerald-400/40 bg-emerald-500/[0.06]"
              : quality.gateStatus === "blocked"
                ? "border-amber-400/50 bg-amber-500/[0.07]"
                : "border-rose-500/40 bg-rose-500/[0.07]",
          )}
        >
          <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
            Impeccable quality gate
          </p>
          <p className="mt-1 text-[12px] text-foreground/90">
            {quality.gateStatus === "passed" && "Passed"}
            {quality.gateStatus === "failed" &&
              `Failed · ${quality.findingCount} finding(s), ${quality.blockers} blocker(s)`}
            {quality.gateStatus === "blocked" &&
              (quality.impeccableAvailable
                ? "Blocked"
                : "Blocked — the detector did not run")}
          </p>
          {quality.iteration > 0 && (
            <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
              correction iteration {quality.iteration}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
