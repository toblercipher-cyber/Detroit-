import { Fragment, useEffect, useState, type ReactNode } from "react";

import dashboardBg from "@/assets/dashboard-bg.jpg";
import { AnimatedText } from "@/components/ui/animated-text";
import {
  DraggableWidgetGrid,
  type WidgetItem,
} from "@/components/ui/draggable-widget-grid";

/* ------------------------------------------------------------------ *
 * DETROIT-AI's own live-ish system board: pipeline runs, uptime, cost,
 * errors, recent builds, review score, stage calls, and token usage
 * across the five models. Data is simulated and updates every few
 * seconds; every chart also has a text alternative for screen readers.
 * ------------------------------------------------------------------ */

type Kind =
  | "runs"
  | "health"
  | "cost"
  | "failures"
  | "traces"
  | "evals"
  | "stages"
  | "models";

interface Widget extends WidgetItem {
  kind: Kind;
}

const WIDGETS: Widget[] = [
  { id: "runs", kind: "runs", size: "wide", label: "Pages built today" },
  { id: "health", kind: "health", size: "sm", label: "System status" },
  { id: "cost", kind: "cost", size: "sm", label: "Compute cost" },
  { id: "failures", kind: "failures", size: "sm", label: "Errors caught" },
  { id: "traces", kind: "traces", size: "wide", label: "Recent builds" },
  { id: "evals", kind: "evals", size: "sm", label: "Review score" },
  { id: "stages", kind: "stages", size: "wide", label: "Pipeline stage calls" },
  { id: "models", kind: "models", size: "wide", label: "Token usage by model" },
];

function useTick(ms = 2000) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => {
      if (!document.hidden) setTick((t) => t + 1);
    }, ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return tick;
}

function noise(seed: number) {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

const fmt = (v: number) => v.toLocaleString("en-US");
const median = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};
const duration = (v: number) =>
  v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`;

type Tone = "ok" | "warn" | "err" | "idle";

const DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  err: "bg-rose-500",
  idle: "bg-muted-foreground/60",
};
const TEXT: Record<Tone, string> = {
  ok: "text-emerald-400",
  warn: "text-amber-300",
  err: "text-rose-400",
  idle: "text-muted-foreground",
};
const ACCENT = "bg-ember";
const HEAT = [
  "bg-foreground/[0.06]",
  "bg-ember/20",
  "bg-ember/40",
  "bg-ember/65",
  "bg-ember",
];

function Shell({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="@container flex h-full flex-col gap-4 p-4 sm:p-5">
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 text-sm leading-none">
        <h3 className="truncate font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
          {title}
        </h3>
        {meta && <span className="shrink-0 text-muted-foreground">{meta}</span>}
      </header>
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </section>
  );
}

function Big({ children, unit }: { children: ReactNode; unit?: string }) {
  return (
    <p className="font-display text-[26px] leading-none font-bold tracking-tight text-foreground tabular-nums @[240px]:text-[30px]">
      {children}
      {unit && (
        <span className="ml-1.5 text-[11px] font-sans font-normal tracking-normal text-muted-foreground">
          {unit}
        </span>
      )}
    </p>
  );
}

function Delta({ value, suffix, good = "up" }: { value: number; suffix?: string; good?: "up" | "down" }) {
  const up = value >= 0;
  const tone: Tone = up === (good === "up") ? "ok" : "err";
  return (
    <span className={`text-[13px] tabular-nums ${TEXT[tone]}`}>
      <span aria-hidden="true">{up ? "↑" : "↓"} </span>
      {Math.abs(value)}%
      {suffix && <span className="text-muted-foreground"> {suffix}</span>}
    </span>
  );
}

function Dot({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  return (
    <span aria-hidden="true" className="relative inline-flex size-2 shrink-0">
      {pulse && (
        <span className={`absolute inset-0 animate-ping rounded-full opacity-50 ${DOT[tone]}`} />
      )}
      <span className={`relative size-2 rounded-full ${DOT[tone]}`} />
    </span>
  );
}

function Row({ children, value }: { children: ReactNode; value: ReactNode }) {
  return (
    <div className="flex items-center gap-2 text-[12px]">
      <dt className="flex min-w-0 items-center gap-2 truncate text-foreground">{children}</dt>
      <dd className="ml-auto text-muted-foreground tabular-nums">{value}</dd>
    </div>
  );
}

/* --- Pages built today: a five-day activity minimap --- */

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Today"];
const SLOTS = 32;
const NOW = 26;
const HOURS = ["12AM", "6AM", "12PM", "6PM"];

function runsAt(day: number, slot: number) {
  const hour = (slot * 45) / 60;
  const shape = 3 + Math.exp(-((hour - 15) ** 2) / 30) * 16 + Math.exp(-((hour - 10) ** 2) / 10) * 9;
  return Math.max(0, Math.round(shape * 2.6 * (0.5 + noise(day * 97 + slot))));
}

function Runs() {
  const t = useTick(2500);
  const today = DAYS.length - 1;
  const grid = DAYS.map((_, d) =>
    Array.from({ length: SLOTS }, (_, s) =>
      d === today && s > NOW ? null : runsAt(d, s) + (d === today && s === NOW ? t % 8 : 0),
    ),
  );
  const peak = Math.max(...grid.flat().map((v) => v ?? 0));
  const total = grid[today].reduce<number>((a, v) => a + (v ?? 0), 0);

  return (
    <Shell title="Pages built today" meta={<Delta value={18} suffix="vs yesterday" />}>
      <Big>{fmt(total)}</Big>
      <div className="mt-auto">
        <div
          role="img"
          aria-label={`Pages built per 45 minutes over the last 5 days. ${fmt(total)} today so far.`}
          className="grid grid-cols-1 items-center gap-x-3 gap-y-[3px] @[480px]:grid-cols-[auto_minmax(0,1fr)]"
        >
          {grid.map((row, d) => (
            <Fragment key={DAYS[d]}>
              <span
                aria-hidden="true"
                className={`hidden text-[11px] leading-none @[480px]:block ${d === today ? "text-foreground" : "text-muted-foreground"}`}
              >
                {DAYS[d]}
              </span>
              <span className="grid grid-cols-[repeat(32,minmax(0,1fr))] gap-[3px]">
                {row.map((v, s) => {
                  const level = v === null || v === 0 ? 0 : Math.max(1, Math.ceil((v / peak) * 4));
                  return (
                    <span
                      key={s}
                      className={`aspect-square rounded-[2.5px] transition-colors duration-700 ${
                        d === today && s === NOW ? "bg-ember" : HEAT[level]
                      }`}
                    />
                  );
                })}
              </span>
            </Fragment>
          ))}
          <span aria-hidden="true" className="hidden @[480px]:block" />
          <span aria-hidden="true" className="mt-2 hidden grid-cols-4 text-[11px] text-muted-foreground @[480px]:grid">
            {HOURS.map((h) => (
              <span key={h}>{h}</span>
            ))}
          </span>
        </div>
      </div>
    </Shell>
  );
}

/* --- System status --- */

const INCIDENTS: Record<number, Tone> = { 11: "warn" };

function Health() {
  const t = useTick(5000);
  const degraded = t % 7 === 6;
  return (
    <Shell title="System status" meta="30d">
      <Big unit="uptime">99.97%</Big>
      <p className={`mt-3 flex items-center gap-2 text-[13px] ${degraded ? TEXT.warn : "text-foreground"}`}>
        <Dot tone={degraded ? "warn" : "ok"} pulse />
        <span className="truncate">{degraded ? "Degraded performance" : "All systems operational"}</span>
      </p>
      <div className="mt-auto">
        <div
          role="img"
          aria-label="Uptime over the last 30 days: 29 days operational, 1 day with degraded performance."
          className="flex h-5 gap-[2px] @[240px]:h-6"
        >
          {Array.from({ length: 30 }, (_, i) => (
            <span key={i} className={`flex-1 rounded-[1.5px] ${INCIDENTS[i] ? "bg-amber-400/80" : "bg-foreground/15"}`} />
          ))}
        </div>
      </div>
    </Shell>
  );
}

/* --- Compute cost --- */

function Cost() {
  const t = useTick(3000);
  const days = Array.from({ length: 14 }, (_, i) => Math.round(7 + noise(i * 5) * 5 + i * 0.25));
  days[13] = Math.round(9 + (t % 30) * 0.15);
  const month = 96.4 + (t % 30) * 0.05;
  const max = Math.max(...days);
  return (
    <Shell title="Compute cost" meta={<Delta value={6} suffix="MoM" good="down" />}>
      <Big unit="MTD">${month.toFixed(0)}</Big>
      <div
        role="img"
        aria-label={`Daily compute cost over the last 14 days, up to $${max}. Today $${days[13]}.`}
        className="mt-auto flex h-10 items-end gap-[3px]"
      >
        {days.map((d, i) => (
          <span
            key={i}
            className={`flex-1 rounded-full transition-[height] duration-700 ${i === days.length - 1 ? ACCENT : "bg-foreground/15"}`}
            style={{ height: `${(d / max) * 100}%` }}
          />
        ))}
      </div>
    </Shell>
  );
}

/* --- Errors caught --- */

function Failures() {
  const t = useTick(5000);
  const causes = [
    { name: "timeout", count: 5 + (Math.floor(t / 3) % 4) },
    { name: "rate-limit", count: 3 },
    { name: "flaky-asset", count: 2 + (Math.floor(t / 5) % 3) },
  ];
  const total = causes.reduce((a, c) => a + c.count, 0) + 1;
  return (
    <Shell title="Errors caught" meta="24h">
      <Big unit="pre-ship">{total}</Big>
      <dl className="mt-auto space-y-2">
        {causes.map((c, i) => (
          <Row key={c.name} value={c.count}>
            <Dot tone={i === 0 ? "err" : "idle"} />
            {c.name}
          </Row>
        ))}
      </dl>
    </Shell>
  );
}

/* --- Recent builds: which model handled the last few pages --- */

const MODEL_NAMES = ["Claude", "Gemini", "Groq", "Qwen", "DeepSeek"];

function trace(n: number) {
  const r = noise(n * 3);
  const tone: Tone = r > 0.92 ? "warn" : "ok";
  return {
    n,
    id: `pg_${Math.floor(noise(n) * 0xffffff).toString(16).padStart(6, "0")}`,
    model: MODEL_NAMES[Math.floor(noise(n * 7) * MODEL_NAMES.length)],
    ms: 600 + noise(n * 13) * 2600,
    tone,
  };
}

const TRACE_WORD: Record<Tone, string> = { ok: "shipped", warn: "slow", err: "error", idle: "pending" };

function Traces() {
  const t = useTick(2200);
  const rows = Array.from({ length: 4 }, (_, i) => trace(t + 40 - i));
  const longest = 3200;
  return (
    <Shell
      title="Recent builds"
      meta={
        <span className="flex items-center gap-1.5">
          <Dot tone="ok" pulse />
          live
        </span>
      }
    >
      <Big unit="p50">{duration(median(rows.map((r) => r.ms)))}</Big>
      <ol aria-label="Most recently built pages" className="mt-auto space-y-2 text-[12px]">
        {rows.map((r, i) => (
          <li
            key={r.n}
            className={`grid-cols-[6px_minmax(0,1fr)_56px] items-center gap-3 @[440px]:grid-cols-[6px_84px_minmax(0,1fr)_22%_56px] ${
              i === 0 ? "text-foreground" : "text-muted-foreground"
            } ${i >= 3 ? "hidden @[520px]:grid" : i === 2 ? "hidden @[360px]:grid" : "grid"}`}
          >
            <Dot tone={r.tone} />
            <span className="truncate">
              {r.id}
              <span className="sr-only">, {TRACE_WORD[r.tone]}, {r.model},</span>
            </span>
            <span aria-hidden="true" className="hidden truncate @[440px]:block">
              {r.model}
            </span>
            <span aria-hidden="true" className="hidden h-[3px] rounded-full bg-foreground/10 @[440px]:block">
              <span
                className={`block h-full rounded-full ${i === 0 ? ACCENT : "bg-foreground/25"}`}
                style={{ width: `${(r.ms / longest) * 100}%` }}
              />
            </span>
            <span className="text-right tabular-nums">{duration(r.ms)}</span>
          </li>
        ))}
      </ol>
    </Shell>
  );
}

/* --- Review score --- */

const EVALS = [
  { name: "Accessibility", value: 0.96 },
  { name: "Performance", value: 0.91 },
  { name: "Security", value: 0.95 },
];

function Evals() {
  const score = EVALS.reduce((a, e) => a + e.value, 0) / EVALS.length;
  return (
    <Shell title="Review score">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <Big>{score.toFixed(2)}</Big>
        <span className={`text-[13px] tabular-nums ${TEXT.ok}`}>
          <span aria-hidden="true">{"↑"} </span>0.02
        </span>
      </div>
      <dl className="mt-auto space-y-2">
        {EVALS.map((e) => (
          <Row key={e.name} value={e.value.toFixed(2)}>
            {e.name}
          </Row>
        ))}
      </dl>
    </Shell>
  );
}

/* --- Pipeline stage calls --- */

const STAGES = [
  { name: "plan", calls: 318 },
  { name: "design", calls: 296 },
  { name: "build", calls: 341 },
  { name: "review", calls: 283 },
];

function StageCalls() {
  const t = useTick(3000);
  const rows = STAGES.map((s, i) => ({ ...s, calls: s.calls + Math.floor((t % 50) * (4 - i) * 0.4) }));
  const max = Math.max(...rows.map((r) => r.calls));
  const total = rows.reduce((a, r) => a + r.calls, 0);
  return (
    <Shell title="Pipeline stage calls" meta="24h">
      <Big>{fmt(total)}</Big>
      <table className="mt-auto w-full table-fixed text-left text-[12px]">
        <caption className="sr-only">Pipeline stage calls in the last 24 hours</caption>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.name} className={i >= 3 ? "hidden @[520px]:table-row" : ""}>
              <th scope="row" className={`w-[110px] truncate py-[6px] pr-3 font-normal ${i === 0 ? "text-foreground" : "text-muted-foreground"}`}>
                {r.name}
              </th>
              <td className="py-[6px]">
                <span aria-hidden="true" className="block h-[3px] rounded-full bg-foreground/10">
                  <span
                    className={`block h-full rounded-full transition-[width] duration-700 ${i === 0 ? ACCENT : "bg-foreground/25"}`}
                    style={{ width: `${(r.calls / max) * 100}%` }}
                  />
                </span>
              </td>
              <td className="w-[52px] py-[6px] text-right text-muted-foreground tabular-nums">{r.calls}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Shell>
  );
}

/* --- Token usage by model --- */

const MODELS = [
  { name: "Claude", share: 0.34, swatch: "bg-ember" },
  { name: "Gemini", share: 0.24, swatch: "bg-ember/70" },
  { name: "Groq", share: 0.18, swatch: "bg-ember/50" },
  { name: "Qwen", share: 0.14, swatch: "bg-ember/30" },
  { name: "DeepSeek", share: 0.1, swatch: "bg-foreground/25" },
];

function Models() {
  return (
    <Shell title="Token usage" meta="by model · 24h">
      <Big unit="tokens">8.7M</Big>
      <dl className="mt-auto grid grid-cols-2 gap-x-6 gap-y-2">
        {MODELS.map((m) => (
          <Row key={m.name} value={`${Math.round(m.share * 100)}%`}>
            <span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${m.swatch}`} />
            <span className="truncate">{m.name}</span>
          </Row>
        ))}
      </dl>
      <div
        role="img"
        aria-label={`Share of tokens: ${MODELS.map((m) => `${m.name} ${Math.round(m.share * 100)}%`).join(", ")}.`}
        className="mt-4 flex h-[3px] gap-[3px]"
      >
        {MODELS.map((m) => (
          <span key={m.name} className={`h-full rounded-full ${m.swatch}`} style={{ width: `${m.share * 100}%` }} />
        ))}
      </div>
    </Shell>
  );
}

const VIEWS: Record<Kind, () => ReactNode> = {
  runs: Runs,
  health: Health,
  cost: Cost,
  failures: Failures,
  traces: Traces,
  evals: Evals,
  stages: StageCalls,
  models: Models,
};

const renderWidget = (item: Widget) => {
  const View = VIEWS[item.kind];
  return <View />;
};

export function DashboardSection() {
  return (
    <section
      id="dashboard"
      className="relative overflow-hidden px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
      style={{
        backgroundImage: `linear-gradient(180deg, rgba(6,6,7,0.9) 0%, rgba(6,6,7,0.72) 25%, rgba(6,6,7,0.88) 75%, rgba(6,6,7,0.98) 100%), url(${dashboardBg})`,
        backgroundSize: "cover",
        backgroundPosition: "center 30%",
      }}
    >
      <div className="mx-auto max-w-6xl">
        <div className="max-w-2xl">
          <AnimatedText
            as="h2"
            text="The system, in the open."
            className="font-display text-3xl font-bold tracking-tight text-foreground sm:text-4xl"
          />
          <AnimatedText
            as="p"
            text="Every run, every model, every dollar — the same board we watch, live. Drag a tile to rearrange it."
            delay={0.2}
            stagger={0.018}
            className="mt-4 text-base leading-relaxed text-muted-foreground sm:text-lg"
          />
        </div>

        <div className="mt-10">
          <DraggableWidgetGrid
            items={WIDGETS}
            renderItem={(item) => renderWidget(item as Widget)}
            maxColumns={4}
            cellSize={200}
            gap={12}
            radius={16}
          />
        </div>
      </div>
    </section>
  );
}
