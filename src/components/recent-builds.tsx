type Palette = {
  bg: string;
  ink: string;
  accent: string;
  dim: string;
};

type Build = {
  name: string;
  kind: string;
  palette: Palette;
  layout: "hero-grid" | "split" | "stacked";
};

const BUILDS: Build[] = [
  {
    name: "Ferndale Roasters",
    kind: "Coffee roastery — landing page",
    palette: { bg: "#231b16", ink: "#f2e6d8", accent: "#e8623a", dim: "#6b5344" },
    layout: "hero-grid",
  },
  {
    name: "Rise Studio",
    kind: "Fitness — waitlist page",
    palette: { bg: "#141a1f", ink: "#e7f1f5", accent: "#3aa8e8", dim: "#3a5a6b" },
    layout: "split",
  },
  {
    name: "Marrow & Co.",
    kind: "Architecture — portfolio",
    palette: { bg: "#181615", ink: "#efe9e4", accent: "#c9a15a", dim: "#5a5044" },
    layout: "stacked",
  },
];

function MiniPage({ palette, layout }: { palette: Palette; layout: Build["layout"] }) {
  const { bg, ink, accent, dim } = palette;

  return (
    <svg
      viewBox="0 0 400 260"
      className="h-full w-full"
      preserveAspectRatio="xMidYMid slice"
      role="img"
      aria-hidden="true"
    >
      <rect width="400" height="260" fill={bg} />

      <rect x="0" y="0" width="400" height="28" fill={bg} />
      <circle cx="20" cy="14" r="3" fill={dim} />
      <rect x="34" y="11" width="46" height="6" rx="3" fill={dim} />
      <rect x="300" y="11" width="20" height="6" rx="3" fill={dim} />
      <rect x="330" y="11" width="20" height="6" rx="3" fill={dim} />
      <rect x="360" y="9" width="24" height="10" rx="2" fill={accent} />

      {layout === "hero-grid" && (
        <>
          <rect x="28" y="56" width="180" height="16" rx="2" fill={ink} />
          <rect x="28" y="80" width="140" height="16" rx="2" fill={ink} opacity="0.85" />
          <rect x="28" y="112" width="110" height="8" rx="2" fill={dim} />
          <rect x="28" y="128" width="90" height="8" rx="2" fill={dim} />
          <rect x="28" y="156" width="78" height="24" rx="3" fill={accent} />
          <rect x="230" y="56" width="142" height="148" rx="4" fill={dim} opacity="0.5" />
          <rect x="250" y="150" width="60" height="8" rx="2" fill={ink} opacity="0.7" />
        </>
      )}

      {layout === "split" && (
        <>
          <rect x="0" y="28" width="190" height="232" fill={dim} opacity="0.35" />
          <rect x="28" y="72" width="130" height="14" rx="2" fill={ink} />
          <rect x="28" y="94" width="130" height="14" rx="2" fill={ink} opacity="0.85" />
          <rect x="28" y="128" width="90" height="8" rx="2" fill={dim} />
          <rect x="28" y="160" width="72" height="22" rx="11" fill={accent} />
          <circle cx="300" cy="150" r="60" fill={accent} opacity="0.18" />
          <rect x="252" y="118" width="96" height="64" rx="4" fill={dim} opacity="0.6" />
        </>
      )}

      {layout === "stacked" && (
        <>
          <rect x="28" y="52" width="220" height="18" rx="2" fill={ink} />
          <rect x="28" y="78" width="150" height="8" rx="2" fill={dim} />
          <rect x="28" y="112" width="106" height="70" rx="3" fill={dim} opacity="0.55" />
          <rect x="146" y="112" width="106" height="70" rx="3" fill={accent} opacity="0.3" />
          <rect x="264" y="112" width="108" height="70" rx="3" fill={dim} opacity="0.55" />
          <rect x="28" y="196" width="60" height="18" rx="9" fill={accent} />
        </>
      )}
    </svg>
  );
}

function BuildTile({ build, className = "" }: { build: Build; className?: string }) {
  return (
    <article
      className={`group relative overflow-hidden ${className}`}
      style={{ backgroundColor: build.palette.bg }}
    >
      <div className="absolute inset-0 transition-transform duration-700 ease-out group-hover:scale-[1.04]">
        <MiniPage palette={build.palette} layout={build.layout} />
      </div>
      <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/20 to-transparent" />
      <div className="relative flex h-full flex-col justify-end p-5">
        <h3 className="font-display text-lg font-bold text-white">
          {build.name}
        </h3>
        <p className="text-sm text-white/70">{build.kind}</p>
      </div>
    </article>
  );
}

export function RecentBuilds() {
  return (
    <div>
      <div className="mb-6 flex items-end justify-between gap-4">
        <h2 className="font-display text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
          Recent builds
        </h2>
        <p className="hidden max-w-[16rem] text-right text-sm text-muted-foreground sm:block">
          Example pages generated end-to-end from a single prompt.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:grid-rows-2 sm:auto-rows-[160px]">
        <BuildTile
          build={BUILDS[0]}
          className="col-span-2 row-span-2 aspect-[4/3] sm:aspect-auto"
        />
        <BuildTile build={BUILDS[1]} className="col-span-2 aspect-[4/3] sm:aspect-auto" />
        <BuildTile build={BUILDS[2]} className="col-span-2 aspect-[4/3] sm:aspect-auto" />
      </div>
    </div>
  );
}
