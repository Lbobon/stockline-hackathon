import type { Config } from "tailwindcss";

const c = (v: string) => `hsl(var(--${v}) / <alpha-value>)`;

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: c("background"),
        foreground: c("foreground"),
        card: c("card"),
        "surface-2": c("surface-2"),
        muted: { DEFAULT: c("muted"), foreground: c("muted-foreground") },
        "secondary-foreground": c("secondary-foreground"),
        border: { DEFAULT: c("border"), hover: c("border-hover") },
        input: c("input"),
        primary: { DEFAULT: c("primary"), hover: c("primary-hover"), foreground: c("primary-foreground") },
        ring: c("ring"),
        "row-selected": c("row-selected"),
        success: c("success"),
        warning: c("warning"),
        destructive: c("destructive"),
        info: c("info"),
        violet: c("violet"),
        "chart-1": c("chart-1"),
        "chart-2": c("chart-2"),
        "chart-3": c("chart-3"),
        "chart-4": c("chart-4"),
        "chart-5": c("chart-5"),
        agent: { voice: c("agent-voice"), fast: c("agent-fast"), scout: c("agent-scout"), scribe: c("agent-scribe"), linguist: c("agent-linguist") },
      },
      borderColor: { DEFAULT: c("border") },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: { base: ["13px", "18px"] },
      boxShadow: { sm: "var(--shadow-sm)", panel: "var(--shadow-panel)", pop: "var(--shadow-pop)" },
      borderRadius: { DEFAULT: "4px" },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-in": { from: { transform: "translateX(12px)", opacity: "0" }, to: { transform: "none", opacity: "1" } },
        flash: { "0%": { backgroundColor: "hsl(var(--violet) / .16)" }, "100%": { backgroundColor: "transparent" } },
        "eq-bar": { "0%,100%": { transform: "scaleY(.35)" }, "50%": { transform: "scaleY(1)" } },
        "soft-pulse": { "0%,100%": { boxShadow: "0 0 0 0 hsl(var(--violet) / .0)" }, "50%": { boxShadow: "0 0 0 3px hsl(var(--violet) / .18)" } },
      },
      animation: {
        "fade-in": "fade-in .12s ease-out",
        "slide-in": "slide-in .16s ease-out",
        flash: "flash 1.6s ease-out",
        "eq-bar": "eq-bar .9s ease-in-out infinite",
        "soft-pulse": "soft-pulse 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
