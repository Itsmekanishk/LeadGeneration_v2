import type { Config } from "tailwindcss";

/**
 * Merged Tailwind config.
 *
 * - shadcn-style hsl-var tokens (--background, --card, …) are kept: legacy
 *   components (auth pages, old ui.tsx) still resolve against them, and the
 *   :root block in app/globals.css bridges them to the LocalLead dark theme.
 * - The design-system palette is merged under an `ll-` prefix so it can never
 *   collide with the shadcn scale (notably `border`, which exists on both
 *   sides with different meanings): `ll-surface`, `ll-indigo`, `ll-border`, …
 * - Fonts point at the variables loaded by app/layout.tsx (next/font) —
 *   no second font-loading system.
 * - darkMode "class": the root layout sets `className="dark"`.
 */
const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
    "./hooks/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // --- legacy (shadcn) tokens, unchanged ---------------------------
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        // --- LocalLead design-system tokens (prefixed to avoid clashes) --
        ll: {
          canvas: "#080C14",
          surface: {
            DEFAULT: "#0D111D",
            elevated: "#131B2E",
          },
          border: {
            DEFAULT: "rgba(255,255,255,0.08)",
            strong: "rgba(255,255,255,0.14)",
          },
          indigo: {
            DEFAULT: "#6366F1",
            soft: "rgba(99,102,241,0.14)",
          },
          cyan: {
            DEFAULT: "#06B6D4",
            soft: "rgba(6,182,212,0.14)",
          },
          confidence: {
            high: "#10B981",
            "high-soft": "rgba(16,185,129,0.14)",
            medium: "#F59E0B",
            "medium-soft": "rgba(245,158,11,0.14)",
            low: "#F43F5E",
            "low-soft": "rgba(244,63,94,0.14)",
          },
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "Inter", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(99,102,241,0.4), 0 0 24px rgba(99,102,241,0.25)",
        card: "0 1px 0 rgba(255,255,255,0.04) inset, 0 8px 24px rgba(0,0,0,0.4)",
      },
      backdropBlur: {
        xl: "24px",
      },
      keyframes: {
        "ping-slow": {
          "0%": { transform: "scale(1)", opacity: "0.6" },
          "100%": { transform: "scale(2.4)", opacity: "0" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
      },
      animation: {
        "ping-slow": "ping-slow 2.2s cubic-bezier(0,0,0.2,1) infinite",
        shimmer: "shimmer 1.8s linear infinite",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
};
export default config;
