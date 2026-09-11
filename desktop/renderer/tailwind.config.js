export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))", input: "hsl(var(--input))", ring: "hsl(var(--ring))",
        background: "hsl(var(--background))", foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))", container: "#a078ff" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        // Tên màu của bản thiết kế Stitch — dùng thẳng trong component mới.
        surface: { lowest: "#0b0e15", low: "#191c23", DEFAULT: "#1d2027", high: "#272a31", highest: "#32353c", bright: "#363941" },
        "on-surface": "#e0e2ec", "on-variant": "#cbc3d7", outline: "#958ea0", "outline-variant": "#494454",
        tertiary: { DEFAULT: "#4edea3", container: "#00a572", on: "#003824", "on-container": "#00311f" },
        info: { DEFAULT: "#adc6ff", container: "#0566d9", on: "#002e6a" },
        warn: "#f59e0b",
        error: { DEFAULT: "#ffb4ab", container: "#93000a", "on-container": "#ffdad6" },
      },
      fontFamily: {
        sans: ["Geist", "Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "SF Mono", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
      keyframes: {
        "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
        "accordion-up": { from: { height: "var(--radix-accordion-content-height)" }, to: { height: "0" } },
      },
    },
  },
  plugins: [import("tailwindcss-animate").then(m => m.default)],
};
