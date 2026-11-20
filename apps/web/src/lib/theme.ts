import { useCallback, useEffect, useState } from "react";
import { readLocal, writeLocal } from "./storage";

export type ThemeMode = "system" | "light" | "dark";

const KEY = "c2b.theme";

function readMode(): ThemeMode {
  const v = readLocal(KEY);
  return v === "light" || v === "dark" ? v : "system";
}

function apply(mode: ThemeMode): void {
  const root = document.documentElement;
  if (mode === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", mode);
}

/** System by default; "light" and "dark" are manual overrides that survive reloads. */
export function useTheme(): { mode: ThemeMode; setMode: (m: ThemeMode) => void } {
  const [mode, setModeState] = useState<ThemeMode>(readMode);

  useEffect(() => {
    apply(mode);
  }, [mode]);

  const setMode = useCallback((m: ThemeMode) => {
    writeLocal(KEY, m);
    setModeState(m);
  }, []);

  return { mode, setMode };
}
