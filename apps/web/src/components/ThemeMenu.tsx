import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useTheme, type ThemeMode } from "../lib/theme";

const LABELS: Record<ThemeMode, string> = {
  system: "Match my system",
  light: "Light",
  dark: "Dark",
};

/** Theme choice: follows the system by default, with a manual override. */
export function ThemeMenu() {
  const { mode, setMode } = useTheme();
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="btn btn-quiet" aria-label={`Colour theme: ${LABELS[mode]}`}>
        <span aria-hidden="true" className="icon">
          {mode === "dark" ? "☾" : mode === "light" ? "☀" : "◐"}
        </span>
        <span>Theme</span>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="menu" align="end" sideOffset={6}>
          <DropdownMenu.Label className="menu-label">Colour theme</DropdownMenu.Label>
          <DropdownMenu.RadioGroup
            value={mode}
            onValueChange={(v) => {
              setMode(v as ThemeMode);
            }}
          >
            {(Object.keys(LABELS) as ThemeMode[]).map((m) => (
              <DropdownMenu.RadioItem key={m} value={m} className="menu-item">
                <span className="menu-check" aria-hidden="true">
                  <DropdownMenu.ItemIndicator>✓</DropdownMenu.ItemIndicator>
                </span>
                {LABELS[m]}
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
