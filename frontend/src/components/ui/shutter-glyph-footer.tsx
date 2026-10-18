"use client"

import * as React from "react"

/**
 * Shutter Glyph Footer — a signal-orange closing section in monospace caps,
 * with the brand set across the full width in a hand-drawn, geometric display
 * alphabet. One letter is swapped for a "shutter": three black blades that
 * meet at a pivot, and the pivot turns to follow the pointer.
 *
 * Top row: a newsletter signup (underlined field, arrow submit, invalid,
 * sending and done states), social links, the copyright, legal links. Every
 * link scrambles through mono glyphs on hover before it settles.
 *
 * The wordmark is SVG drawn here: every letter is built from rectangles,
 * slanted strokes and elliptical bands, so it looks the same on any page and
 * no font is loaded. Hover a letter and it slices along its waist; click one
 * and it flips; click the shutter and its blades rotate a quarter turn.
 *
 * No dependencies and nothing fetched: React is the only import.
 */

// #region glyphs
// Pure: the alphabet, the shutter, layout and small helpers. Lifted out and run
// by the test.

export const clamp = (v: number, lo: number, hi: number): number => (v < lo ? lo : v > hi ? hi : v)

/** Cap height of every glyph, in SVG units. The wordmark's viewBox is this tall. */
export const CAP = 100
const S = 19 // stem
const B = 17 // bar

const f = (n: number): string => String(Math.round(n * 100) / 100)

/** Axis-aligned box. */
export const rect = (x: number, y: number, w: number, h: number): string =>
  "M" + f(x) + " " + f(y) + "H" + f(x + w) + "V" + f(y + h) + "H" + f(x) + "Z"

/** A slanted stroke `t` wide measured horizontally, from (x1, y1) at the top to (x2, y2) at the bottom. */
export const slant = (x1: number, y1: number, x2: number, y2: number, t: number): string =>
  "M" + f(x1) + " " + f(y1) + "H" + f(x1 + t) + "L" + f(x2 + t) + " " + f(y2) + "H" + f(x2) + "Z"

/** Closed polygon from [x, y, x, y, …]. */
export const poly = (pts: number[]): string => {
  let d = ""
  for (let i = 0; i + 1 < pts.length; i += 2) d += (i ? "L" : "M") + f(pts[i]) + " " + f(pts[i + 1])
  return d + "Z"
}

const at = (cx: number, cy: number, rx: number, ry: number, deg: number): string => {
  const a = (deg * Math.PI) / 180
  return f(cx + rx * Math.cos(a)) + " " + f(cy + ry * Math.sin(a))
}

/**
 * A band between an outer ellipse (rx, ry) and an inner one (rx - tx, ry - ty),
 * from angle a0 to a1 in degrees. Angles grow clockwise on screen (0 = right,
 * 90 = bottom); a1 < a0 runs the other way.
 */
export const band = (cx: number, cy: number, rx: number, ry: number, tx: number, ty: number, a0: number, a1: number): string => {
  const large = Math.abs(a1 - a0) > 180 ? 1 : 0
  const cw = a1 > a0 ? 1 : 0
  const irx = rx - tx
  const iry = ry - ty
  return (
    "M" + at(cx, cy, rx, ry, a0) +
    "A" + f(rx) + " " + f(ry) + " 0 " + large + " " + cw + " " + at(cx, cy, rx, ry, a1) +
    "L" + at(cx, cy, irx, iry, a1) +
    "A" + f(irx) + " " + f(iry) + " 0 " + large + " " + (1 - cw) + " " + at(cx, cy, irx, iry, a0) +
    "Z"
  )
}

/** A full elliptical ring (needs fill-rule evenodd). */
export const ring = (cx: number, cy: number, rx: number, ry: number, tx: number, ty: number): string => {
  const e = (r1: number, r2: number) =>
    "M" + f(cx - r1) + " " + f(cy) +
    "A" + f(r1) + " " + f(r2) + " 0 1 1 " + f(cx + r1) + " " + f(cy) +
    "A" + f(r1) + " " + f(r2) + " 0 1 1 " + f(cx - r1) + " " + f(cy) + "Z"
  return e(rx, ry) + e(rx - tx, ry - ty)
}

/** A D-shaped bowl: flat on the left, half an ellipse on the right (needs evenodd). */
export const bowl = (x: number, y: number, w: number, h: number, t: number, tb: number): string => {
  const ry = h / 2
  const rx = Math.min(w - t, ry * 0.95)
  const sx = x + w - rx
  return (
    "M" + f(x) + " " + f(y) + "H" + f(sx) +
    "A" + f(rx) + " " + f(ry) + " 0 0 1 " + f(sx) + " " + f(y + h) + "H" + f(x) + "Z" +
    "M" + f(x + t) + " " + f(y + tb) + "H" + f(sx) +
    "A" + f(rx - t) + " " + f(ry - tb) + " 0 0 1 " + f(sx) + " " + f(y + h - tb) + "H" + f(x + t) + "Z"
  )
}

export type Glyph = { w: number; d: string[] }

const SPACE: Glyph = { w: 42, d: [] }

/** The display alphabet, A–Z. Each glyph is CAP tall; its shapes overlap into one letter. */
export const GLYPHS: Record<string, Glyph> = {
  A: { w: 96, d: [slant(37, 0, 0, 100, 23), slant(37, 0, 73, 100, 23), rect(18, 60, 60, 16)] },
  B: { w: 84, d: [rect(0, 0, S, 100), bowl(0, 0, 78, 52, S, B), bowl(0, 52 - B, 84, 100 - 52 + B, S, B)] },
  C: { w: 90, d: [band(45, 50, 45, 50, S + 2, B, -40, -320)] },
  D: { w: 90, d: [rect(0, 0, S + 1, 100), bowl(0, 0, 90, 100, S + 2, B)] },
  E: { w: 72, d: [rect(0, 0, S, 100), rect(0, 0, 72, B), rect(0, 41.5, 64, B), rect(0, 100 - B, 72, B)] },
  F: { w: 70, d: [rect(0, 0, S, 100), rect(0, 0, 70, B), rect(0, 43, 62, B)] },
  G: { w: 94, d: [band(47, 50, 47, 50, S + 2, B, -38, -360), rect(48, 46, 46, B)] },
  H: { w: 86, d: [rect(0, 0, S, 100), rect(86 - S, 0, S, 100), rect(0, 42, 86, B)] },
  I: { w: S + 2, d: [rect(0, 0, S + 2, 100)] },
  J: { w: 72, d: [rect(72 - S, 0, S, 64), band(36, 62, 36, 38, S, B, 0, 180)] },
  K: { w: 86, d: [rect(0, 0, S, 100), slant(62, 0, 10, 62, 24), slant(30, 42, 62, 100, 24)] },
  L: { w: 68, d: [rect(0, 0, S, 100), rect(0, 100 - B, 68, B)] },
  M: { w: 112, d: [rect(0, 0, S + 3, 100), slant(0, 0, 46, 100, 9), slant(68, 0, 40, 100, 22), rect(112 - S - 3, 0, S + 3, 100)] },
  N: { w: 88, d: [rect(0, 0, S, 100), rect(88 - S, 0, S, 100), slant(0, 0, 63, 100, 25)] },
  O: { w: 100, d: [ring(50, 50, 50, 50, S + 2, B)] },
  P: { w: 80, d: [rect(0, 0, S, 100), bowl(0, 0, 80, 60, S, B)] },
  Q: { w: 100, d: [ring(50, 50, 50, 50, S + 2, B), slant(50, 62, 78, 100, 22)] },
  R: { w: 84, d: [rect(0, 0, S, 100), bowl(0, 0, 82, 58, S, B), slant(34, 50, 62, 100, 22)] },
  // Two bands that meet on the spine: r = (CAP + B) / 4 makes it exactly B thick.
  S: { w: 80, d: [band(40, 29.25, 40, 29.25, S, B, -22, -273), band(40, 70.75, 40, 29.25, S, B, -93, 158)] },
  T: { w: 82, d: [rect(0, 0, 82, B), rect(41 - S / 2 - 1, 0, S + 2, 100)] },
  // Stems run 2 past the band so the joins never show a hairline.
  U: { w: 86, d: [rect(0, 0, S, 60), rect(86 - S, 0, S, 60), band(43, 58, 43, 42, S, B, 0, 180)] },
  V: { w: 94, d: [slant(0, 0, 36, 100, 22), slant(72, 0, 36, 100, 22)] },
  W: { w: 132, d: [slant(0, 0, 26, 100, 20), slant(56, 0, 26, 100, 20), slant(56, 0, 86, 100, 20), slant(112, 0, 86, 100, 20)] },
  X: { w: 92, d: [slant(0, 0, 68, 100, 24), slant(68, 0, 0, 100, 24)] },
  Y: { w: 92, d: [slant(0, 0, 35, 56, 22), slant(70, 0, 35, 56, 22), rect(35, 50, 22, 50)] },
  Z: { w: 80, d: [rect(0, 0, 80, B), rect(0, 100 - B, 80, B), slant(56, B - 1, 0, 100 - B + 1, 24)] },
}

/** Any character outside A–Z is a gap. */
export const glyphOf = (ch: string): Glyph => GLYPHS[ch.toUpperCase()] ?? SPACE

/** The shutter is a square, one cap tall. */
export const SHUTTER = 100
/** Blade width along the edges it touches. */
const K = 21
/** Where the pivot rests, in the shutter's own (unrotated) frame. */
export const REST = 77

/** Rotate (x, y) by `turns` quarter turns clockwise about the shutter's centre. */
export const turn = (x: number, y: number, turns: number): [number, number] => {
  const n = ((turns % 4) + 4) % 4
  let a = x
  let b = y
  for (let i = 0; i < n; i++) {
    const t = a
    a = SHUTTER - b
    b = t
  }
  return [a, b]
}

/** Where the pivot rests for a given rotation. */
export const restPivot = (turns: number): [number, number] => turn(REST, REST, turns)

/**
 * The shutter's three blades for a pivot at (px, py), rotated `turns` quarter
 * turns. Built in the unrotated frame — one thin wedge from the top-left
 * corner, two wide ones along the top and left edges — then turned.
 */
export const blades = (px: number, py: number, turns: number): number[][] => {
  const [qx, qy] = turn(clamp(px, 8, 92), clamp(py, 8, 92), -turns)
  const shapes = [
    [0, 0, K, 0, qx, qy, 0, K],
    [qx, 0, SHUTTER, 0, SHUTTER, K, qx, qy],
    [0, qy, qx, qy, K, SHUTTER, 0, SHUTTER],
  ]
  return shapes.map((s) => {
    const out: number[] = []
    for (let i = 0; i < s.length; i += 2) out.push(...turn(s[i], s[i + 1], turns))
    return out
  })
}

export type Placed = { ch: string; x: number; w: number; shutter: boolean }

/** Lay the word out left to right with a fixed gap. `shutterAt` swaps that letter for the shutter. */
export const layout = (word: string, shutterAt: number, gap: number = 12): { items: Placed[]; width: number } => {
  const items: Placed[] = []
  let x = 0
  Array.from(word).forEach((ch, i) => {
    const shutter = i === shutterAt && ch.trim() !== ""
    const w = shutter ? SHUTTER : glyphOf(ch).w
    items.push({ ch, x, w, shutter })
    x += w + gap
  })
  return { items, width: Math.max(1, x - (items.length ? gap : 0)) }
}

/** Frame-rate independent ease toward a target: `k` is the fraction closed per 1/60s. */
export const approach = (from: number, to: number, k: number, dt: number): number =>
  to + (from - to) * Math.pow(1 - clamp(k, 0, 1), clamp(dt, 0, 0.1) * 60)

const NOISE = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&*+=/<>"

/**
 * One frame of the link scramble. Letters left of `progress` (0 → 1) are
 * settled; the rest show a noise glyph picked from `seed`. Spaces and
 * punctuation never scramble, so the line keeps its shape.
 */
export const scramble = (text: string, progress: number, seed: number): string => {
  const chars = Array.from(text)
  const settled = Math.floor(clamp(Number.isFinite(progress) ? progress : 1, 0, 1) * chars.length)
  return chars
    .map((ch, i) => {
      if (i < settled || !/[a-z0-9]/i.test(ch)) return ch
      const r = Math.abs(Math.sin((i + 1) * 12.9898 + seed * 78.233) * 43758.5453) % 1
      return NOISE[Math.floor(r * NOISE.length)]
    })
    .join("")
}

/** Loose email check: something@something.tld, no spaces. */
export const isEmail = (v: string): boolean => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test((v || "").trim())

/** "#f9531f" / "#f53" → [249, 83, 31]. Anything unparseable is null. */
export const parseHex = (hex: string): [number, number, number] | null => {
  const m = /^#?([\da-f]{3}|[\da-f]{6})$/i.exec((hex || "").trim())
  if (!m) return null
  const h = m[1].length === 3 ? m[1].replace(/./g, (c) => c + c) : m[1]
  const n = parseInt(h, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** Mix two hex colours, `t` of the way from a to b. Unparseable input returns `a` as given. */
export const mixHex = (a: string, b: string, t: number): string => {
  const x = parseHex(a)
  const y = parseHex(b)
  if (!x || !y) return a
  const k = clamp(Number.isFinite(t) ? t : 0, 0, 1)
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * k).toString(16).padStart(2, "0")).join("")
}

// #endregion

export type FooterLink = { label: string; href?: string }

export type ShutterGlyphFooterProps = {
  brand?: string
  /** What the giant wordmark spells. Defaults to `brand`. Letters A–Z; anything else is a gap. */
  wordmark?: string
  /** Index of the letter the shutter replaces. `-1` for none. */
  shutterAt?: number
  company?: string
  since?: number
  year?: number
  signupLabel?: string
  placeholder?: string
  /**
   * Called with a valid address. Return or resolve `false`, or throw, to show
   * the error state. Without it the form pretends to send.
   */
  onSubscribe?: (email: string) => void | boolean | Promise<void | boolean>
  socials?: FooterLink[]
  legal?: FooterLink[]
  onLinkClick?: (label: string, href?: string) => void
  background?: string
  /**
   * The colour at the very top of the panel, fading into `background`.
   * Defaults to `background` lifted 13% toward white — right for the original
   * signal-orange panel, wrong for a dark one, where lifting toward white
   * greys the warmth out. Pass a warm surface instead.
   */
  backgroundTop?: string
  ink?: string
  /**
   * Emphasis colour: the shutter blades, the submit arrow, link hover, focus
   * rings. Defaults to `ink`, so a caller that does not set it gets exactly
   * the original two-colour component.
   *
   * This exists because the component had no accent channel at all. Dropped
   * into a design system that carries emphasis on one hue, it rendered as a
   * flat two-tone slab with the house accent nowhere in it.
   */
  accent?: string
  /**
   * Ink for the giant wordmark alone. Defaults to `ink`. Separate because the
   * wordmark is full-bleed: at body-text contrast it becomes the loudest thing
   * on the page, which is the wrong job for a sign-off.
   *
   * Must be opaque. Each letter is drawn twice — an upper and a lower half on
   * clip paths that overlap by ~1.2 units at the waist — so a translucent ink
   * double-paints that band and draws a bright stripe across every glyph. To
   * dim the wordmark, mix toward the background rather than lowering alpha.
   */
  markInk?: string
  fontMono?: string
  /** `false` holds the shutter still and skips the reveal and scramble. */
  animate?: boolean
  className?: string
}

const DEFAULT_SOCIALS: FooterLink[] = [
  { label: "Instagram", href: "#" },
  { label: "Linked In", href: "#" },
  { label: "Spotify", href: "#" },
]

const DEFAULT_LEGAL: FooterLink[] = [
  { label: "Terms & Conditions", href: "#" },
  { label: "Privacy Policy", href: "#" },
  { label: "Cookies Policy", href: "#" },
]

const MONO =
  '"JetBrains Mono", "IBM Plex Mono", "Space Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace'

// One scoped stylesheet. Every rule sits under .sgf; element resets go through
// :where(.sgf) so they never out-rank the component's own classes or yours.
const CSS =
  ".sgf{position:relative;isolation:isolate;overflow:hidden;container-type:inline-size;color:var(--sgf-ink);background:linear-gradient(180deg,var(--sgf-top) 0%,var(--sgf-bg) 24%);font-family:var(--sgf-mono);font-size:clamp(11px,1.12cqw,13.5px);line-height:1.18;letter-spacing:.015em;text-transform:uppercase;-webkit-font-smoothing:antialiased}" +
  ".sgf ::selection{background:var(--sgf-ink);color:var(--sgf-bg)}" +
  ":where(.sgf) a{color:inherit;text-decoration:none}" +
  ":where(.sgf) button{font:inherit;color:inherit;letter-spacing:inherit;text-transform:inherit;background:none;border:0;padding:0;margin:0;cursor:pointer}" +
  ":where(.sgf) ul{list-style:none;margin:0;padding:0}" +
  ":where(.sgf) p{margin:0}" +
  ".sgf svg{max-width:none}" +
  ".sgf .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}" +
  ".sgf-top{box-sizing:border-box;display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr) minmax(0,2fr) auto;column-gap:24px;row-gap:36px;padding:clamp(64px,11.5cqw,150px) clamp(16px,2.6cqw,40px) 0}" +
  ".sgf-fade{transition:opacity .8s ease var(--sgf-d,0ms),translate .8s cubic-bezier(.2,.7,.1,1) var(--sgf-d,0ms)}" +
  ".sgf[data-in='false'] .sgf-fade{opacity:0;translate:0 10px}" +
  // Signup.
  ".sgf-form{max-width:266px}" +
  ".sgf-lede{max-width:232px}" +
  ".sgf-field{position:relative;display:flex;align-items:center;margin-top:clamp(18px,2.2cqw,26px);padding:4px 0 5px;border-bottom:1px solid var(--sgf-ink)}" +
  ".sgf-field::after{content:'';position:absolute;left:0;right:0;bottom:-2px;height:2px;background:var(--sgf-accent);transform:scaleX(0);transform-origin:0 50%;transition:transform .5s cubic-bezier(.2,.8,.2,1)}" +
  ".sgf-field:focus-within::after{transform:scaleX(1)}" +
  ".sgf-field[data-state='error']{animation:sgf-shake .42s cubic-bezier(.36,.07,.19,.97)}" +
  "@keyframes sgf-shake{20%,60%{translate:-5px 0}40%,80%{translate:5px 0}}" +
  ".sgf-input{flex:1;min-width:0;font:inherit;letter-spacing:inherit;text-transform:uppercase;color:inherit;background:transparent;border:0;outline:none;padding:0;margin:0;border-radius:0;-webkit-appearance:none;appearance:none}" +
  ".sgf-input::placeholder{color:var(--sgf-ink);opacity:1;transition:opacity .25s}" +
  ".sgf-input:focus::placeholder{opacity:.4}" +
  ".sgf-go{flex:none;display:grid;place-items:center;width:26px;height:20px;margin-right:-6px;color:var(--sgf-accent)}" +
  ".sgf-go svg{width:11px;height:11px;transition:translate .35s cubic-bezier(.2,.8,.2,1)}" +
  ".sgf-go:hover svg,.sgf-go:focus-visible svg{translate:4px 0}" +
  ".sgf-go:focus-visible{outline:2px solid var(--sgf-accent);outline-offset:3px}" +
  ".sgf-spin{display:inline-block;width:1ch;text-align:center}" +
  ".sgf-done{display:flex;align-items:center;gap:10px}" +
  ".sgf-done svg{width:12px;height:12px;flex:none;color:var(--sgf-accent)}" +
  ".sgf-done button{margin-left:auto;opacity:.55;transition:opacity .2s}" +
  ".sgf-done button:hover,.sgf-done button:focus-visible{opacity:1;outline:none;text-decoration:underline;text-underline-offset:3px}" +
  ".sgf-msg{min-height:1.2em;margin-top:8px;font-size:.86em;opacity:.85}" +
  // Links: a mono label, an arrow that walks right, an underline that wipes in.
  ".sgf-list{display:flex;flex-direction:column;gap:clamp(5px,.6cqw,7px)}" +
  ".sgf-link{position:relative;display:inline-flex;align-items:center;gap:.65em;white-space:nowrap;padding:1px 0;transition:color .2s ease}" +
  ".sgf-link:hover,.sgf-link:focus-visible{color:var(--sgf-accent)}" +
  ".sgf-link::after{content:'';position:absolute;left:0;right:1.6em;bottom:-2px;height:1px;background:currentColor;transform:scaleX(0);transform-origin:100% 50%;transition:transform .4s cubic-bezier(.2,.8,.2,1)}" +
  ".sgf-link:hover::after,.sgf-link:focus-visible::after{transform:scaleX(1);transform-origin:0 50%}" +
  ".sgf-link svg{width:.8em;height:.8em;flex:none;transition:translate .35s cubic-bezier(.2,.8,.2,1)}" +
  ".sgf-link:hover svg,.sgf-link:focus-visible svg{translate:5px 0}" +
  // Was `outline:none`, leaving keyboard users nothing but the underline wipe
  // the pointer also gets. A real ring, matching the project's focus style.
  ".sgf-link:focus-visible{outline:2px solid var(--sgf-accent);outline-offset:3px}" +
  ".sgf-copy{white-space:pre-line}" +
  // The wordmark.
  ".sgf-mark{box-sizing:border-box;padding:clamp(36px,4.6cqw,64px) clamp(16px,2.6cqw,40px) clamp(14px,2.1cqw,28px)}" +
  ".sgf-svg{display:block;width:100%;height:auto;overflow:hidden;fill:var(--sgf-mark)}" +
  ".sgf-rise{transition:transform 1.15s cubic-bezier(.16,.84,.2,1) var(--sgf-d,0ms)}" +
  ".sgf[data-in='false'] .sgf-rise{transform:translateY(112px)}" +
  ".sgf-g{cursor:pointer}" +
  ".sgf-flip{transform-box:fill-box;transform-origin:50% 50%}" +
  ".sgf-flip.is-flip{animation:sgf-flip .7s cubic-bezier(.2,.8,.2,1)}" +
  "@keyframes sgf-flip{0%{transform:scaleY(1)}45%{transform:scaleY(-1)}100%{transform:scaleY(1)}}" +
  // Was cubic-bezier(.2,.9,.25,1.15): the final control point above 1 makes
  // the letter halves overshoot and settle back. The anti-slop detector flags
  // elastic easing, and this is the project's own curve (`AnimatedText`).
  ".sgf-half{transition:transform .45s cubic-bezier(.16,1,.3,1)}" +
  ".sgf-g:hover .sgf-up{transform:translate(5px,-1.5px)}" +
  ".sgf-g:hover .sgf-dn{transform:translate(-5px,1.5px)}" +
  // The shutter is the one thing in the wordmark that moves and responds, so
  // it is the right place to spend the accent.
  ".sgf-sh polygon{transition:none;fill:var(--sgf-accent)}" +
  "@container (max-width: 760px){.sgf-top{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.sgf-top>:first-child,.sgf-top>.sgf-copy{grid-column:1 / -1}.sgf-copy{order:3}.sgf-form{max-width:360px}}" +
  "@container (max-width: 420px){.sgf-top{grid-template-columns:minmax(0,1fr)}}" +
  "@media (prefers-reduced-motion: reduce){.sgf .sgf-rise,.sgf .sgf-fade,.sgf .sgf-half,.sgf .sgf-link svg,.sgf .sgf-link::after,.sgf .sgf-field::after,.sgf .sgf-go svg{transition:none!important}.sgf .sgf-field,.sgf .sgf-flip{animation:none!important}.sgf[data-in='false'] .sgf-rise{transform:none}.sgf[data-in='false'] .sgf-fade{opacity:1;translate:none}}"

const Arrow = () => (
  <svg viewBox="0 0 12 12" aria-hidden="true">
    <path d="M1 6h9.2M6.4 2.2 10.2 6l-3.8 3.8" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="square" />
  </svg>
)

const Check = () => (
  <svg viewBox="0 0 12 12" aria-hidden="true">
    <path d="M1.5 6.4 4.6 9.4 10.6 2.6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="square" />
  </svg>
)

const external = (href?: string) => (href && /^https?:/.test(href) ? { target: "_blank", rel: "noreferrer" } : {})

/** A mono link whose label scrambles through noise glyphs on hover, then settles. */
function ScrambleLink({
  link,
  still,
  onFollow,
  style,
}: {
  link: FooterLink
  still: boolean
  onFollow: (e: React.MouseEvent<HTMLAnchorElement>, l: FooterLink) => void
  style?: React.CSSProperties
}) {
  const textRef = React.useRef<HTMLSpanElement>(null)
  const raf = React.useRef(0)
  const text = link.label

  const run = () => {
    const el = textRef.current
    if (still || !el) return
    cancelAnimationFrame(raf.current)
    const t0 = performance.now()
    const seed = Math.random() * 100
    let n = 0
    const step = (now: number) => {
      const p = (now - t0) / 420
      if (p >= 1) {
        el.textContent = text
        return
      }
      // A new noise glyph every other frame reads as typing, not static.
      if (n++ % 2 === 0) el.textContent = scramble(text, p, seed + Math.floor(n / 2))
      raf.current = requestAnimationFrame(step)
    }
    raf.current = requestAnimationFrame(step)
  }

  React.useEffect(() => () => cancelAnimationFrame(raf.current), [])

  return (
    <li className="sgf-fade" style={style}>
      <a
        className="sgf-link"
        href={link.href || "#"}
        aria-label={text}
        onPointerEnter={run}
        onFocus={run}
        onClick={(e) => onFollow(e, link)}
        {...external(link.href)}
      >
        <span ref={textRef} aria-hidden="true">
          {text}
        </span>
        <Arrow />
      </a>
    </li>
  )
}

type FormState = "idle" | "error" | "sending" | "done" | "failed"

export default function ShutterGlyphFooter({
  brand = "Vanda",
  wordmark,
  shutterAt = 2,
  company,
  since = 2020,
  year = new Date().getFullYear(),
  signupLabel = "Sign up for the latest work, news & insights",
  placeholder = "Email address",
  onSubscribe,
  socials = DEFAULT_SOCIALS,
  legal = DEFAULT_LEGAL,
  onLinkClick,
  background = "#f9531f",
  backgroundTop,
  ink = "#0c0c0c",
  accent,
  markInk,
  fontMono = MONO,
  animate = true,
  className = "",
}: ShutterGlyphFooterProps) {
  const word = ((wordmark ?? brand).trim() || brand).toUpperCase()
  const { items, width } = React.useMemo(() => layout(word, shutterAt), [word, shutterAt])
  const shutter = items.find((it) => it.shutter)

  const uid = React.useId().replace(/[^a-zA-Z0-9_-]/g, "")
  const upId = "sgf-up-" + uid
  const dnId = "sgf-dn-" + uid

  const rootRef = React.useRef<HTMLElement>(null)
  const svgRef = React.useRef<SVGSVGElement>(null)
  const bladeRefs = React.useRef<(SVGPolygonElement | null)[]>([])
  const ptr = React.useRef({ x: 0, y: 0, inside: false })
  const [turns, setTurns] = React.useState(0)
  const pivot = React.useRef<[number, number]>(restPivot(0))

  const [reduced, setReduced] = React.useState(false)
  React.useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
    const onMq = () => setReduced(mq.matches)
    onMq()
    mq.addEventListener("change", onMq)
    return () => mq.removeEventListener("change", onMq)
  }, [])
  const still = reduced || !animate

  // Reveal once, the first time the footer is on screen; the shutter only
  // tracks while it is.
  const [seen, setSeen] = React.useState(false)
  const [visible, setVisible] = React.useState(false)
  React.useEffect(() => {
    const root = rootRef.current
    if (!root) return
    const io = new IntersectionObserver(
      ([e]) => {
        setVisible(e.isIntersecting)
        if (e.isIntersecting) setSeen(true)
      },
      { threshold: 0.1 },
    )
    io.observe(root)
    return () => io.disconnect()
  }, [])

  // The shutter's pivot eases toward the pointer, written straight to the
  // polygons so tracking never re-renders React.
  React.useEffect(() => {
    if (!shutter) return
    const paint = (x: number, y: number) => {
      blades(x, y, turns).forEach((pts, i) => {
        bladeRefs.current[i]?.setAttribute("points", pts.map(f).join(" "))
      })
    }
    const rest = restPivot(turns)
    if (still || !visible) {
      pivot.current = rest
      paint(rest[0], rest[1])
      return
    }
    let raf = 0
    let last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      const p = ptr.current
      const tx = p.inside ? clamp(p.x, 8, 92) : rest[0]
      const ty = p.inside ? clamp(p.y, 8, 92) : rest[1]
      const [x, y] = pivot.current
      pivot.current = [approach(x, tx, 0.12, dt), approach(y, ty, 0.12, dt)]
      paint(pivot.current[0], pivot.current[1])
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [shutter, still, visible, turns])

  const onPointer = (e: React.PointerEvent<HTMLElement>) => {
    const svg = svgRef.current
    const p = ptr.current
    p.inside = e.type !== "pointerleave"
    if (!svg || !shutter) return
    // Pointer in the shutter's own units, from the SVG's on-screen box.
    const r = svg.getBoundingClientRect()
    const scale = r.width / width || 1
    p.x = (e.clientX - r.left) / scale - shutter.x
    p.y = (e.clientY - r.top) / scale
  }

  const flip = (e: React.MouseEvent<SVGGElement>) => {
    if (still) return
    const el = e.currentTarget
    el.classList.remove("is-flip")
    void el.getBoundingClientRect()
    el.classList.add("is-flip")
  }

  const follow = (e: React.MouseEvent<HTMLAnchorElement>, l: FooterLink) => {
    // "#" and missing hrefs never touch the host page's URL hash.
    if (!l.href || l.href === "#") e.preventDefault()
    onLinkClick?.(l.label, l.href)
  }

  // ---- signup ----------------------------------------------------------------
  const [email, setEmail] = React.useState("")
  const [state, setState] = React.useState<FormState>("idle")
  const [shake, setShake] = React.useState(0)
  const [spin, setSpin] = React.useState(0)
  const inputRef = React.useRef<HTMLInputElement>(null)
  const alive = React.useRef(true)
  React.useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // The shake remounts the field (a fresh key restarts its animation), so the
  // caret is put back afterwards.
  React.useEffect(() => {
    if (shake && state === "error") inputRef.current?.focus()
  }, [shake])

  React.useEffect(() => {
    if (state !== "sending" || still) return
    const id = window.setInterval(() => setSpin((s) => s + 1), 90)
    return () => window.clearInterval(id)
  }, [state, still])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (state === "sending") return
    if (!isEmail(email)) {
      setState("error")
      setShake((n) => n + 1)
      return
    }
    setState("sending")
    let ok = true
    try {
      if (onSubscribe) ok = (await onSubscribe(email.trim())) !== false
      else await new Promise((r) => setTimeout(r, 1100))
    } catch {
      ok = false
    }
    if (!alive.current) return
    setState(ok ? "done" : "failed")
    if (!ok) setShake((n) => n + 1)
  }

  const reset = () => {
    setEmail("")
    setState("idle")
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const message =
    state === "error"
      ? "That doesn't look like an email"
      : state === "failed"
        ? "Couldn't sign you up. Try again"
        : state === "sending"
          ? "Sending"
          : ""

  // ---- render ----------------------------------------------------------------
  const vars = {
    "--sgf-bg": background,
    "--sgf-top": backgroundTop ?? mixHex(background, "#ffffff", 0.13),
    "--sgf-ink": ink,
    "--sgf-accent": accent ?? ink,
    "--sgf-mark": markInk ?? ink,
    "--sgf-mono": fontMono,
  } as React.CSSProperties

  let d = 0
  const delay = () => ({ "--sgf-d": (d += 60) + "ms" }) as React.CSSProperties
  const label = company ?? "The " + brand + " Creative Co."
  const years = since && since < year ? since + "—" + year : String(year)

  return (
    <footer
      ref={rootRef}
      className={"sgf " + className}
      style={vars}
      data-in={seen || still ? "true" : "false"}
      onPointerMove={onPointer}
      onPointerEnter={onPointer}
      onPointerLeave={onPointer}
    >
      <style>{CSS}</style>

      <div className="sgf-top">
        <form className="sgf-form sgf-fade" style={delay()} onSubmit={submit} noValidate>
          <label htmlFor={"sgf-email-" + uid} className="sgf-lede" style={{ display: "block" }}>
            {signupLabel}
          </label>
          <div className="sgf-field" data-state={state} key={shake}>
            {state === "done" ? (
              <div className="sgf-done" role="status">
                <Check />
                <span>You&apos;re on the list</span>
                <button type="button" onClick={reset}>
                  Undo
                </button>
              </div>
            ) : (
              <>
                <input
                  ref={inputRef}
                  id={"sgf-email-" + uid}
                  className="sgf-input"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  spellCheck={false}
                  placeholder={placeholder}
                  value={email}
                  aria-invalid={state === "error" || undefined}
                  aria-describedby={"sgf-msg-" + uid}
                  disabled={state === "sending"}
                  onChange={(e) => {
                    setEmail(e.target.value)
                    if (state === "error" || state === "failed") setState("idle")
                  }}
                />
                <button className="sgf-go" type="submit" aria-label="Subscribe" disabled={state === "sending"}>
                  {state === "sending" ? (
                    <span className="sgf-spin" aria-hidden="true">
                      {still ? "…" : "|/-\\"[spin % 4]}
                    </span>
                  ) : (
                    <Arrow />
                  )}
                </button>
              </>
            )}
          </div>
          <p id={"sgf-msg-" + uid} className="sgf-msg" aria-live="polite">
            {message}
          </p>
        </form>

        {socials.length > 0 && (
          <nav aria-label="Social">
            <ul className="sgf-list">
              {socials.map((l, i) => (
                <ScrambleLink key={l.label + i} link={l} still={still} onFollow={follow} style={delay()} />
              ))}
            </ul>
          </nav>
        )}

        <p className="sgf-copy sgf-fade" style={delay()}>
          {"© " + years + "\n" + label}
        </p>

        {legal.length > 0 && (
          <nav aria-label="Legal">
            <ul className="sgf-list">
              {legal.map((l, i) => (
                <ScrambleLink key={l.label + i} link={l} still={still} onFollow={follow} style={delay()} />
              ))}
            </ul>
          </nav>
        )}
      </div>

      <div className="sgf-mark">
        <p className="sr-only">{word}</p>
        <svg
          ref={svgRef}
          className="sgf-svg"
          viewBox={"0 0 " + f(width) + " " + CAP}
          style={{ aspectRatio: width + " / " + CAP }}
          aria-hidden="true"
          focusable="false"
        >
          <defs>
            {/* Every letter is drawn twice, cut at its waist; hover slides the halves apart. */}
            <clipPath id={upId} clipPathUnits="userSpaceOnUse">
              <rect x={-40} y={-200} width={400} height={252.6} />
            </clipPath>
            <clipPath id={dnId} clipPathUnits="userSpaceOnUse">
              <rect x={-40} y={51.4} width={400} height={250} />
            </clipPath>
          </defs>
          {items.map((it, i) => {
            if (it.ch.trim() === "") return null
            const rise = { "--sgf-d": 200 + i * 90 + "ms" } as React.CSSProperties
            if (it.shutter) {
              const rest = restPivot(turns)
              return (
                <g key={i} transform={"translate(" + f(it.x) + " 0)"}>
                  <g className="sgf-rise" style={rise}>
                    <g className="sgf-g sgf-sh" onClick={() => setTurns((t) => t + 1)}>
                      <rect width={SHUTTER} height={CAP} fill="transparent" />
                      {blades(rest[0], rest[1], turns).map((pts, b) => (
                        <polygon
                          key={b}
                          ref={(el) => {
                            bladeRefs.current[b] = el
                          }}
                          points={pts.map(f).join(" ")}
                        />
                      ))}
                    </g>
                  </g>
                </g>
              )
            }
            const g = glyphOf(it.ch)
            const paths = g.d.map((p, k) => <path key={k} d={p} fillRule="evenodd" />)
            return (
              <g key={i} transform={"translate(" + f(it.x) + " 0)"}>
                <g className="sgf-rise" style={rise}>
                  <g
                    className="sgf-g sgf-flip"
                    onClick={flip}
                    onAnimationEnd={(e) => e.currentTarget.classList.remove("is-flip")}
                  >
                    <rect width={g.w} height={CAP} fill="transparent" />
                    <g clipPath={"url(#" + upId + ")"}>
                      <g className="sgf-half sgf-up">{paths}</g>
                    </g>
                    <g clipPath={"url(#" + dnId + ")"}>
                      <g className="sgf-half sgf-dn">{paths}</g>
                    </g>
                  </g>
                </g>
              </g>
            )
          })}
        </svg>
      </div>
    </footer>
  )
}
