---
name: Obsidian Kinetic
colors:
  surface: '#131315'
  surface-dim: '#131315'
  surface-bright: '#39393b'
  surface-container-lowest: '#0e0e10'
  surface-container-low: '#1c1b1d'
  surface-container: '#201f22'
  surface-container-high: '#2a2a2c'
  surface-container-highest: '#353437'
  on-surface: '#e5e1e4'
  on-surface-variant: '#c7c4d7'
  inverse-surface: '#e5e1e4'
  inverse-on-surface: '#313032'
  outline: '#908fa0'
  outline-variant: '#464554'
  surface-tint: '#c0c1ff'
  primary: '#c0c1ff'
  on-primary: '#1000a9'
  primary-container: '#8083ff'
  on-primary-container: '#0d0096'
  inverse-primary: '#494bd6'
  secondary: '#cebdff'
  on-secondary: '#381385'
  secondary-container: '#4f319c'
  on-secondary-container: '#bea8ff'
  tertiary: '#d3bbff'
  on-tertiary: '#3f0689'
  tertiary-container: '#a37af1'
  on-tertiary-container: '#37007c'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e1e0ff'
  primary-fixed-dim: '#c0c1ff'
  on-primary-fixed: '#07006c'
  on-primary-fixed-variant: '#2f2ebe'
  secondary-fixed: '#e8ddff'
  secondary-fixed-dim: '#cebdff'
  on-secondary-fixed: '#21005e'
  on-secondary-fixed-variant: '#4f319c'
  tertiary-fixed: '#ebdcff'
  tertiary-fixed-dim: '#d3bbff'
  on-tertiary-fixed: '#260059'
  on-tertiary-fixed-variant: '#572ba0'
  background: '#131315'
  on-background: '#e5e1e4'
  surface-variant: '#353437'
  obsidian-bg: '#020617'
  electric-indigo: '#818CF8'
  muted-lavender: '#C4B5FD'
  glow-violet: '#D8B4FE'
  surface-glass: rgba(15, 23, 42, 0.6)
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 72px
    fontWeight: '800'
    lineHeight: 80px
    letterSpacing: -0.04em
  display-lg-mobile:
    fontFamily: Inter
    fontSize: 40px
    fontWeight: '800'
    lineHeight: 48px
    letterSpacing: -0.02em
  headline-xl:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-xl-mobile:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-mono:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.05em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 8px
  container-max: 1280px
  gutter: 24px
  margin-desktop: 64px
  margin-mobile: 20px
  section-gap: 120px
---

## Brand & Style

The design system is engineered for a sophisticated, high-tech experience centered around an "agentic buddy." The brand personality is intelligent, helpful, and quietly powerful—represented through a "Deep Obsidian" canvas that feels infinite and futuristic. 

The aesthetic blends **Minimalism** with **Glassmorphism**. High-contrast typography sits atop translucent layers, while "Electric Indigo" accents and glowing border effects simulate the presence of a digital consciousness. The interface should feel like a premium command center: precise, responsive, and ethereal.

## Colors

The palette is anchored in a dark-mode-first architecture. 

- **Primary & Secondary:** Used for active states, primary actions, and "buddy" presence indicators.
- **Backgrounds:** Use `obsidian-bg` for the base layer. Interactive surfaces use `surface-glass` with a backdrop-filter (blur) to create depth.
- **Accents:** The `glow-violet` is reserved for delicate border highlights and soft radial gradients that follow the cursor or agentic movements.
- **Neutral:** Grays are suppressed in favor of deep indigo-tinted blacks to maintain a premium "ink" feel.

## Typography

This design system utilizes **Inter** for its clean, systematic legibility and **Geist** (or a similar monospaced font) for technical labels to reinforce the "agentic/code" nature of the product.

Headlines should use tight letter-spacing to look impactful against dark backgrounds. Long-form body text maintains generous line height for readability. Use the `label-mono` style for small metadata, status indicators, or "system logs" associated with the agentic cursor.

## Layout & Spacing

The layout follows a **Fixed Grid** model on desktop, centering content within a 1280px container to create a focused, editorial feel. 

- **Rhythm:** An 8px linear scale governs all padding and margins. 
- **Sectioning:** Large vertical gaps (`section-gap`) provide breathing room, allowing the glowing background effects to "bleed" between content blocks.
- **Responsive:** On mobile, margins shrink to 20px, and the 12-column desktop grid collapses into a single-column flow. Content cards should stretch to fill the width minus margins.

## Elevation & Depth

Visual hierarchy is achieved through **Glassmorphism** and **Tonal Layering** rather than traditional shadows.

1.  **Base Layer:** Solid `#020617`.
2.  **Mid Layer (Cards/Panels):** `surface-glass` (60% opacity) with a `blur(12px)` and a 1px border.
3.  **Active Layer:** Glowing borders. Use a `conic-gradient` or `linear-gradient` of `electric-indigo` and `glow-violet` as a mask for the border to create a "tracing" light effect.
4.  **Shadows:** When necessary, use extremely diffused, low-opacity shadows tinted with `#4C1D95` (e.g., `0 20px 50px rgba(76, 29, 149, 0.3)`) to simulate a light source from the UI elements themselves.

## Shapes

The design system uses a **Rounded** (0.5rem) base language. This balances the "high-tech" precision of sharp edges with the "friendly/helpful" nature of the agent. 

- **Buttons & Inputs:** `rounded` (8px).
- **Large Cards:** `rounded-lg` (16px).
- **Agent Avatars:** Always circular to suggest a holistic, omni-directional presence.
- **Glass Containers:** Use a consistent 1px stroke (inner border) to define the edge of rounded shapes against the dark background.

## Components

- **Buttons:** Primary buttons use a solid `electric-indigo` fill with white text. Secondary buttons are "Ghost" style with a 1px `glow-violet` border and a subtle hover glow.
- **Glow Borders:** A signature component. Apply a thin, semi-transparent violet stroke to cards that "lights up" when the agentic cursor hovers near them.
- **Chips:** Small, rounded-full capsules with `tertiary_color` backgrounds and `muted-lavender` text for tagging features or status.
- **Input Fields:** Dark, recessed backgrounds with `surface-glass` properties. The focus state should trigger a subtle external violet glow.
- **The "Buddy" Cursor:** A custom cursor component featuring a soft radial violet flare (`blur(20px)`) that follows the system pointer, symbolizing the "BashIn" agent.
- **Lists:** Clean, borderless rows with horizontal dividers using `rgba(255,255,255,0.05)`.