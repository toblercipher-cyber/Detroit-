# Product

> ## ⚠️ LEGACY DOCUMENT — NOT AUTHORITATIVE
>
> This file was created for the **original Landing Page build only**. Its scope
> statements (for example "scoped to the marketing landing page only, specifically
> its first section" and "the rest of the pipeline is explicitly out of scope — do
> not build toward it") describe that earlier, narrower task.
>
> - It is retained for **historical and design context** only.
> - It is **NOT authoritative** for backend scope, full-product scope, current
>   frontend scope, or architecture decisions.
> - Do **not** derive a product specification from it, and do not treat its
>   out-of-scope statements as current constraints.
> - Its record of the approved Landing Page's visual direction and content
>   constraints (no fabricated testimonials, logos, pricing or benchmarks)
>   remains useful background.
>
> **Current authority order:** `CLAUDE.md` → `skills.md` → `docs/` → this file.
>
> The rest of this file is intentionally left unrewritten so the original build
> context stays intact.

<!-- impeccable:product-schema 1 -->


## Platform

web

## Stack

Vite + React + TypeScript + Tailwind CSS + shadcn/ui, single `index.html` entry point wired to the TS/TSX source (Vite's standard module-script pattern) so the user can run one local dev server and see live edits. User-specified, non-negotiable.

## Users

Primary users are people who want to turn a prompt/idea into a live landing page without writing code themselves (founders, indie hackers, marketers). The user I'm building for (toblercipher) is the founder building this tool.

## Product Purpose

DETROIT-AI is an AI-powered landing-page builder, positioned similarly to Lovable: the user describes what they want and the product produces a working landing page. Success today is narrowly scoped to shipping the marketing site's hero/first section — not the product app itself.

## Positioning

Differentiator (per founder, for future reference — not required for this build): an explicit pipeline of HITL (human-in-the-loop) → Planning → Designing → Building → Review checks → Security checks → MCP server integration, rather than a single opaque generation step.

## Operating Context

This session's task is scoped to the marketing landing page only, specifically its first section (everything in the reference comp except the bottom footer row). The rest of the pipeline (planning/designing/building/review/security/MCP features) is explicitly out of scope for now per the founder's instruction — do not build toward it.

## Capabilities and Constraints

- Reference comp: `Refrence_images/Gemini_Generated_Image_go11n1go11n1go11.png` — dark, editorial studio-site layout (split hero: headline + methodology/video on the left, portfolio grid + countdown + contact form on the right). This is directional/visual reference to reskin with DETROIT-AI branding and copy, not literal copy to reuse (original comp is for a fictional "Obsidian Studio" design/architecture studio).
- Background image asset: same file, `Refrence_images/Gemini_Generated_Image_go11n1go11n1go11.png` (mountain/lava environment), to be used as the hero background.
- Content status: no real project screenshots, testimonials, or finalized copy exist yet. Placeholder content in the reference comp's tone/register is acceptable for now; founder will supply real content later.

## Brand Commitments

- Name: DETROIT-AI.

## Evidence on Hand

- `Refrence_images/Gemini_Generated_Image_go11n1go11n1go11.png` (comp + background source)
- `Refrence_images/Firefly.png`, `Refrence_images/Gemini_Generated_Image_5z9hrt5z9hrt5z9h.png`, `Refrence_images/Gemini_Generated_Image_5z9hrt5z9hrt5z9h (1).png` (unused so far; not referenced by the founder for this task)
- No testimonials, customer logos, pricing, or benchmarks exist — do not fabricate them.

## Product Principles

- Ship only what's asked: the hero/first section, nothing further into the pipeline described above.
- Match the reference comp's visual structure and mood, rebranded to DETROIT-AI.
- Keep the stack simple to run locally (single dev server, single HTML entry) per founder's explicit constraint.
