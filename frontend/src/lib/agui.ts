/**
 * AG-UI stream client: live agent activity, over the authenticated API.
 *
 * Why this file exists:
 * `watchProjectStatus` polls `GET /status` every 1.5s and can only ever show a
 * stage name. The backend now publishes real AG-UI events from the running
 * graph, and this subscribes to them.
 *
 * Layer: frontend infrastructure (`src/lib/`), beside `api.ts`. It renders
 * nothing and imports nothing from `components/` or `pages/`.
 *
 * ---------------------------------------------------------------------------
 * WHY fetch AND NOT EventSource
 *
 * The browser's `EventSource` cannot send an `Authorization` header, and this
 * stream is owner-scoped: it carries one user's project state and is bound to
 * their verified Supabase identity. Moving the credential into a query string
 * to satisfy EventSource would put a bearer token in server logs and browser
 * history (`docs/security.md` §2). So the stream is read with `fetch` plus a
 * stream reader — a few more lines, and the token stays in a header.
 *
 * WHY NOT @ag-ui/client's HttpAgent
 *
 * `HttpAgent` models "POST a run and stream its response". DETROIT's run is
 * already executing in a background task before any browser attaches, so an
 * agent that starts runs would start a second one. The event TYPES here are
 * the official `@ag-ui/core` definitions; only the attach semantics differ,
 * and they differ because the architecture genuinely does.
 *
 * SSE PARSING
 * Deliberately minimal: this endpoint emits `data:` lines and `:` comments and
 * nothing else, so a full SSE implementation would be unused weight. Comments
 * are keepalives and are skipped.
 * ---------------------------------------------------------------------------
 */

import { EventType, type BaseEvent } from "@ag-ui/core";

import { API_BASE_URL } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";

/** One entry in the visible timeline. Derived only from real events. */
export interface ActivityEntry {
  id: string;
  node: string;
  label: string;
  /** Semantic tone from the backend; the component maps it to colour. */
  tone: string;
  detail: string;
  status: "running" | "done" | "failed";
  at: number;
}

/** The run state the stream reports, mirroring `STATE_SNAPSHOT`. */
export interface RunSnapshot {
  projectId: string;
  stage: string;
  status: string;
  iterationCount: number;
  previewAvailable: boolean;
  awaitingCheckpointId: string | null;
  lastError: { code: string; message: string } | null;
}

export interface QualityVerdict {
  gateStatus: "passed" | "failed" | "blocked";
  impeccableAvailable: boolean;
  findingCount: number;
  blockers: number;
  iteration: number;
}

export type ConnectionState =
  | "connecting"
  | "open"
  | "closed"
  | "error"
  | "unsupported";

export interface AguiHandlers {
  onSnapshot?: (snapshot: RunSnapshot) => void;
  onActivity?: (entry: ActivityEntry) => void;
  onQuality?: (verdict: QualityVerdict) => void;
  onHitl?: (checkpointId: string | null) => void;
  onConnection?: (state: ConnectionState, detail?: string) => void;
  /** The run ended. The caller decides whether to refetch anything. */
  onFinished?: () => void;
}

/** `CUSTOM` events DETROIT defines. Core AG-UI has no event for these. */
const CUSTOM_ACTIVITY = "detroit.activity";
const CUSTOM_QUALITY = "detroit.quality";
const CUSTOM_HITL = "detroit.hitl";
const CUSTOM_STAGE_ERROR = "detroit.stage_error";

/**
 * Attach to a project's event stream. Returns a function that detaches.
 *
 * Reconnection is the caller's choice, not an automatic loop here: a run that
 * has finished should not be reconnected to, and only the caller knows that.
 */
export function subscribeToRun(
  projectId: string,
  handlers: AguiHandlers,
): () => void {
  const controller = new AbortController();
  let stopped = false;

  const emit = (state: ConnectionState, detail?: string) =>
    handlers.onConnection?.(state, detail);

  void (async () => {
    emit("connecting");
    let response: Response;
    try {
      const token = await getAccessToken();
      response = await fetch(
        `${API_BASE_URL}/api/v1/projects/${encodeURIComponent(projectId)}/agui`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
        },
      );
    } catch {
      if (!stopped) emit("error", "Could not reach the builder backend.");
      return;
    }

    if (!response.ok || !response.body) {
      if (!stopped) {
        emit(
          "error",
          response.status === 401
            ? "Your session expired. Sign in again to watch this build."
            : `The activity stream is unavailable (${response.status}).`,
        );
      }
      return;
    }

    emit("open");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done || stopped) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line.
        let split: number;
        while ((split = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data: ")) continue; // `:` keepalive
            try {
              dispatch(JSON.parse(line.slice(6)) as BaseEvent, handlers);
            } catch {
              /* A frame we cannot parse is skipped, never fatal: one bad
                 event must not end a build's live view. */
            }
          }
        }
      }
      if (!stopped) {
        emit("closed");
        handlers.onFinished?.();
      }
    } catch {
      if (!stopped) emit("error", "The activity stream was interrupted.");
    } finally {
      reader.cancel().catch(() => undefined);
    }
  })();

  return () => {
    stopped = true;
    controller.abort();
  };
}

let sequence = 0;

function dispatch(event: BaseEvent, handlers: AguiHandlers): void {
  switch (event.type) {
    case EventType.STATE_SNAPSHOT: {
      const snapshot = (event as { snapshot?: RunSnapshot }).snapshot;
      if (snapshot) handlers.onSnapshot?.(snapshot);
      return;
    }
    case EventType.RUN_ERROR: {
      const message = (event as { message?: string }).message ?? "The run failed.";
      handlers.onActivity?.({
        id: `err-${++sequence}`,
        node: "run",
        label: message,
        tone: "error",
        detail: "",
        status: "failed",
        at: Date.now(),
      });
      return;
    }
    case EventType.RUN_FINISHED:
      handlers.onFinished?.();
      return;
    case EventType.TOOL_CALL_START: {
      const e = event as { toolCallId?: string; toolCallName?: string };
      handlers.onActivity?.({
        id: `tool-${e.toolCallId ?? ++sequence}`,
        node: "tool",
        label: `Calling ${e.toolCallName ?? "a tool"}`,
        tone: "tool",
        detail: "",
        status: "running",
        at: Date.now(),
      });
      return;
    }
    case EventType.TOOL_CALL_RESULT: {
      const e = event as { toolCallId?: string; content?: string };
      handlers.onActivity?.({
        id: `tool-${e.toolCallId ?? ++sequence}`,
        node: "tool",
        label: "Tool finished",
        tone: "tool",
        detail: truncate(e.content ?? ""),
        status: "done",
        at: Date.now(),
      });
      return;
    }
    case EventType.CUSTOM: {
      const e = event as { name?: string; value?: Record<string, unknown> };
      const value = e.value ?? {};
      if (e.name === CUSTOM_ACTIVITY) {
        handlers.onActivity?.({
          id: `act-${++sequence}`,
          node: String(value.node ?? ""),
          label: String(value.label ?? ""),
          tone: String(value.tone ?? "running"),
          detail: truncate(String(value.detail ?? "")),
          status: "running",
          at: Date.now(),
        });
      } else if (e.name === CUSTOM_QUALITY) {
        handlers.onQuality?.(value as unknown as QualityVerdict);
      } else if (e.name === CUSTOM_HITL) {
        handlers.onHitl?.((value.checkpointId as string | null) ?? null);
      } else if (e.name === CUSTOM_STAGE_ERROR) {
        handlers.onActivity?.({
          id: `stage-err-${++sequence}`,
          node: String(value.node ?? ""),
          label: String(value.message ?? "A stage failed"),
          tone: "error",
          detail: String(value.code ?? ""),
          status: "failed",
          at: Date.now(),
        });
      }
      return;
    }
    default:
      // STEP_STARTED / STEP_FINISHED carry the node name, which `detroit.activity`
      // already presents with a readable label. Ignored rather than shown twice.
      return;
  }
}

function truncate(text: string, limit = 160): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= limit ? flat : `${flat.slice(0, limit - 1)}…`;
}
