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
        agent: { voice: c("agent-voice"), fast: c("agent-fast"), scout: c("agent-scout"), scribe: c("agent-scribe"), linguist: c("agent-linguist"), guard: c("agent-guard") },
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
        // le Garde-fou: a verdict stamps in; a blocked sentence flashes red; the check sweeps while Jev reads
        stamp: { "0%": { transform: "scale(.4)", opacity: "0" }, "60%": { transform: "scale(1.18)", opacity: "1" }, "100%": { transform: "scale(1)" } },
        "guard-flash": { "0%": { backgroundColor: "hsl(var(--destructive) / .28)" }, "100%": { backgroundColor: "hsl(var(--destructive) / .06)" } },
        "guard-in": { from: { transform: "translateY(-6px)", opacity: "0" }, to: { transform: "none", opacity: "1" } },
        sweep: { "0%": { backgroundPosition: "-120% 0" }, "100%": { backgroundPosition: "220% 0" } },
      },
      animation: {
        "fade-in": "fade-in .12s ease-out",
        "slide-in": "slide-in .16s ease-out",
        flash: "flash 1.6s ease-out",
        "eq-bar": "eq-bar .9s ease-in-out infinite",
        "soft-pulse": "soft-pulse 1.4s ease-in-out infinite",
        stamp: "stamp .32s cubic-bezier(.2,.9,.3,1.3)",
        "guard-flash": "guard-flash 1.2s ease-out forwards",
        "guard-in": "guard-in .22s ease-out",
        sweep: "sweep 1.1s linear infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
