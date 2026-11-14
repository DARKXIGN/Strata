import { useEffect, useState } from "react";

export type Theme = "light" | "contrast";

const STORAGE_KEY = "strata-theme";

/** Light (default) or high-contrast dark (black background, white text, bright blue accent). Remembered per browser. */
export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === "contrast" ? "contrast" : "light";
    } catch {
      return "light";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* storage unavailable: theme still applies for this visit */
    }
  }, [theme]);
  return [theme, setTheme];
}

/** Chart colors per theme (Recharts takes literal colors, not CSS variables). */
export const CHART_COLORS: Record<Theme, { latency: string; jitter: string; grid: string; axis: string; marker: string; surface: string }> = {
  light: { latency: "#2563EB", jitter: "#8B95A1", grid: "#E5E8EB", axis: "#8B95A1", marker: "#4E5968", surface: "#FFFFFF" },
  contrast: { latency: "#6AA8FF", jitter: "#FFFFFF", grid: "#2E2E2E", axis: "#C4CAD1", marker: "#9AA4AF", surface: "#0B0B0B" },
};
