import { Github, Heart, MousePointer2 } from "lucide-react";

import mcpBg from "@/assets/mcp-bg.jpg";
import { AnimatedText } from "@/components/ui/animated-text";
import { IntegrationDiagram } from "@/components/ui/integration-diagram";

const ClaudeMark = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 28 28" fill="none" className={className}>
    <path
      d="M5.488 18.62L11.004 15.54L11.088 15.26L11.004 15.12H10.724L9.8 15.064L6.664 14.98L3.92 14.84L1.26 14.7L0.588 14.56L0 13.72L0.056 13.3L0.616 12.936L1.428 12.992L3.192 13.132L5.852 13.3L7.784 13.412L10.64 13.748H11.088L11.144 13.552L11.004 13.44L10.892 13.328L8.12 11.48L5.152 9.52L3.584 8.372L2.744 7.812L2.324 7.252L2.156 6.076L2.912 5.236L3.948 5.32L4.2 5.376L5.236 6.188L7.476 7.896L10.36 10.08L10.78 10.416L10.948 10.304L10.976 10.22L10.78 9.912L9.24 7L7.56 4.088L6.804 2.884L6.608 2.156C6.524 1.876 6.496 1.596 6.496 1.316L7.336 0.14L7.84 0L9.016 0.168L9.464 0.56L10.192 2.24L11.34 4.844L13.16 8.372L13.72 9.436L14 10.388L14.084 10.668H14.28V10.528L14.42 8.512L14.7 6.076L14.98 2.94L15.064 2.044L15.512 0.98L16.352 0.42L17.08 0.728L17.64 1.54L17.556 2.044L17.248 4.2L16.52 7.588L16.1 9.884H16.352L16.632 9.576L17.78 8.064L19.712 5.656L20.552 4.676L21.56 3.64L22.204 3.136H23.408L24.276 4.452L23.884 5.824L22.652 7.392L21.616 8.708L20.132 10.696L19.236 12.292L19.32 12.404H19.516L22.876 11.676L24.668 11.368L26.796 11.004L27.776 11.452L27.888 11.9L27.496 12.852L25.2 13.412L22.512 13.972L18.508 14.896L18.452 14.924L18.508 15.008L20.3 15.176L21.084 15.232H22.988L26.516 15.512L27.44 16.072L27.972 16.828L27.888 17.388L26.46 18.116L24.556 17.668L20.076 16.604L18.564 16.24H18.34V16.352L19.628 17.612L21.952 19.712L24.92 22.428L25.06 23.1L24.696 23.66L24.304 23.604L21.728 21.644L20.72 20.804L18.48 18.9H18.34V19.096L18.844 19.852L21.588 23.968L21.728 25.228L21.532 25.62L20.804 25.9L20.048 25.732L18.424 23.492L16.744 20.972L15.428 18.676L15.288 18.788L14.476 27.244L14.112 27.664L13.272 28L12.572 27.44L12.18 26.6L12.572 24.864L13.02 22.624L13.384 20.832L13.72 18.62L13.916 17.892V17.836H13.72L12.04 20.16L9.52 23.604L7.504 25.732L7.028 25.928L6.188 25.508L6.272 24.724L6.72 24.08L9.52 20.496L11.2 18.284L12.32 16.996L12.292 16.856H12.208L4.816 21.672L3.5 21.84L2.94 21.28L2.996 20.44L3.276 20.16L5.516 18.62H5.488Z"
      fill="currentColor"
    />
  </svg>
);

const SERVERS = [
  {
    icon: Github,
    name: "GitHub",
    description: "Reads your repo and ships pages as pull requests, not file dumps.",
  },
  {
    icon: ClaudeMark,
    name: "Claude",
    description: "The primary planner and reviewer at every stage of the pipeline.",
  },
  {
    icon: MousePointer2,
    name: "Cursor",
    description: "Open a generated project locally and keep editing where you left off.",
  },
  {
    icon: Heart,
    name: "Lovable",
    description: "Hand a build off to iterate on it visually, no context lost.",
  },
];

export function McpSection() {
  return (
    <section
      id="mcp"
      className="relative overflow-hidden px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
      style={{
        backgroundImage: `linear-gradient(180deg, rgba(4,4,6,0.92) 0%, rgba(4,4,6,0.8) 28%, rgba(4,4,6,0.9) 76%, rgba(4,4,6,0.98) 100%), url(${mcpBg})`,
        backgroundSize: "cover",
        backgroundPosition: "center 30%",
      }}
    >
      <div className="mx-auto grid w-full max-w-6xl grid-cols-1 items-start gap-12 lg:grid-cols-[1fr_1fr] lg:gap-16">
        <div>
          <AnimatedText
            as="h2"
            text="Wired into your tools, not just ours."
            className="font-display text-3xl font-bold tracking-tight text-foreground sm:text-4xl"
          />
          <AnimatedText
            as="p"
            text="DETROIT-AI connects over the Model Context Protocol (MCP), so the pipeline can act inside the tools you already work in — starting with four servers."
            delay={0.2}
            stagger={0.014}
            className="mt-5 max-w-md text-base leading-relaxed text-foreground/80"
          />

          <p className="mt-6 font-mono text-xs uppercase tracking-[0.18em] text-ember">
            4 connected MCP servers
          </p>
          <ul className="mt-4 space-y-4">
            {SERVERS.map((server) => (
              <li key={server.name} className="group flex items-start gap-3">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-white/15 bg-white/5 text-foreground transition-all duration-200 group-hover:-translate-y-0.5 group-hover:border-ember/60 group-hover:text-ember group-hover:shadow-[0_8px_20px_-8px_rgba(235,94,53,0.6)]">
                  <server.icon className="h-4 w-4" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-foreground">{server.name}</p>
                  <p className="text-sm leading-relaxed text-foreground/75">
                    {server.description}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-3xl border border-white/10 bg-white/[0.03] p-4 pb-6 backdrop-blur-sm sm:p-6 sm:pb-9">
          <IntegrationDiagram />
        </div>
      </div>
    </section>
  );
}
