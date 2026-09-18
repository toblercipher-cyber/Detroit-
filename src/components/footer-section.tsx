import { useRef, useState, type FormEvent } from "react";
import { motion, useInView, type Variants } from "framer-motion";
import { Github, Linkedin, Twitter, Youtube } from "lucide-react";

import heroBg from "@/assets/hero-bg.jpg";
import { AnimatedGroup } from "@/components/ui/animated-group";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const NAVIGATE_LINKS = [
  { label: "Pipeline", href: "#pipeline" },
  { label: "Agents", href: "#agents" },
  { label: "About", href: "#about" },
  { label: "System", href: "#dashboard" },
  { label: "Harness", href: "#harness" },
  { label: "Connect", href: "#mcp" },
];

const COMPANY_LINKS = ["About", "Careers", "Blog", "Press", "Contact", "Privacy"];

const SOCIALS = [
  { icon: Github, label: "GitHub", href: "#" },
  { icon: Twitter, label: "X", href: "#" },
  { icon: Linkedin, label: "LinkedIn", href: "#" },
  { icon: Youtube, label: "YouTube", href: "#" },
];

const containerVariants: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.1,
      delayChildren: 0.1,
    },
  },
};

const itemVariants: Variants = {
  hidden: { opacity: 0, y: 20 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.5 },
  },
};

export function FooterSection({
  title = "Ready to ship your next build?",
}: {
  title?: string;
}) {
  const [submitted, setSubmitted] = useState(false);
  const titleRef = useRef(null);
  const isTitleInView = useInView(titleRef, { once: true, margin: "-100px" });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitted(true);
  }

  return (
    <footer
      className="relative overflow-hidden px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
      style={{
        backgroundImage: `linear-gradient(180deg, rgba(10,8,7,0.94) 0%, rgba(10,8,7,0.88) 30%, rgba(10,8,7,0.96) 78%, rgba(10,8,7,1) 100%), url(${heroBg})`,
        backgroundSize: "cover",
        backgroundPosition: "center 55%",
      }}
    >
      <motion.div
        className="mx-auto max-w-6xl"
        variants={containerVariants}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true }}
      >
        {/* CTA banner */}
        <motion.div
          className="relative h-[140px] w-full overflow-hidden rounded-2xl bg-gradient-to-br from-ember to-ember-dim md:h-[260px]"
          variants={itemVariants}
        >
          <svg
            className="pointer-events-none absolute inset-0 h-full w-full opacity-90 mix-blend-multiply"
            xmlns="http://www.w3.org/2000/svg"
          >
            <filter id="footerNoise">
              <feTurbulence
                type="fractalNoise"
                baseFrequency="0.65"
                numOctaves="4"
                stitchTiles="stitch"
              />
            </filter>
            <rect width="100%" height="100%" filter="url(#footerNoise)" />
          </svg>

          <div
            ref={titleRef}
            className="pointer-events-none absolute inset-0 flex items-center justify-center px-6"
          >
            {isTitleInView && (
              <AnimatedGroup
                className="flex flex-wrap justify-center gap-x-3 text-center font-display text-2xl font-bold text-white md:text-4xl lg:text-5xl"
                preset="slide"
              >
                {title.split(" ").map((word, i) => (
                  <span key={`${word}-${i}`} className="inline-block">
                    {word}
                  </span>
                ))}
              </AnimatedGroup>
            )}
          </div>
        </motion.div>

        {/* Newsletter + link columns */}
        <div className="mt-12 flex flex-col justify-between gap-12 md:mt-16 lg:flex-row lg:gap-8">
          <motion.div
            className="flex flex-col justify-between space-y-10 lg:w-1/3"
            variants={itemVariants}
          >
            <div className="space-y-4">
              <h3 className="font-display text-lg font-bold text-foreground">
                Newsletter
              </h3>
              {submitted ? (
                <p className="max-w-sm text-sm text-foreground/80">
                  You're on the list — we'll be in touch.
                </p>
              ) : (
                <form
                  onSubmit={handleSubmit}
                  className="flex max-w-sm items-center gap-3"
                >
                  <Input
                    type="email"
                    placeholder="Enter your email"
                    required
                    className="h-11"
                  />
                  <Button type="submit" size="default">
                    Submit
                  </Button>
                </form>
              )}
            </div>

            <div className="space-y-4">
              <ul className="flex items-center gap-4">
                {SOCIALS.map((social) => (
                  <li key={social.label}>
                    <a
                      href={social.href}
                      aria-label={social.label}
                      className="flex h-9 w-9 items-center justify-center rounded-full border border-white/15 text-foreground/70 transition-colors hover:border-ember hover:text-ember"
                    >
                      <social.icon size={16} />
                    </a>
                  </li>
                ))}
              </ul>
              <p className="text-sm text-foreground/50">
                &copy; {new Date().getFullYear()} DETROIT-AI. All rights reserved.
              </p>
            </div>
          </motion.div>

          <motion.div
            className="flex flex-wrap gap-10 lg:w-1/2 lg:justify-end lg:gap-20"
            variants={itemVariants}
          >
            <div className="flex flex-col space-y-4">
              <h4 className="font-display text-sm font-bold text-foreground">
                Navigate
              </h4>
              <ul className="flex flex-col space-y-2">
                {NAVIGATE_LINKS.map((link) => (
                  <li key={link.href}>
                    <a
                      href={link.href}
                      className="font-mono text-xs uppercase tracking-[0.12em] text-foreground/60 transition-colors hover:text-ember"
                    >
                      {link.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>

            <div className="flex flex-col space-y-4">
              <h4 className="font-display text-sm font-bold text-foreground">
                Company
              </h4>
              <ul className="flex flex-col space-y-2">
                {COMPANY_LINKS.map((item) => (
                  <li key={item}>
                    <a
                      href="#"
                      className="font-mono text-xs uppercase tracking-[0.12em] text-foreground/60 transition-colors hover:text-ember"
                    >
                      {item}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          </motion.div>
        </div>
      </motion.div>
    </footer>
  );
}
