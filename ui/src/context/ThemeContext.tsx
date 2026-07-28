// Dark mode (added 2026-07-28) -- class-based (Tailwind v4's
// `@custom-variant dark (&:where(.dark, .dark *))` in index.css), not
// prefers-color-scheme-only, so the toggle in NavBar can override the OS
// setting. Defaults to the OS preference on first load, then remembers
// whatever the user picked (localStorage), same persistence pattern as
// SessionContext.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const STORAGE_KEY = "ar_copilot_theme";
type Theme = "light" | "dark";

function loadInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // ignore -- fall through to OS preference
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

interface ThemeState {
  theme: Theme;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(loadInitialTheme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // localStorage unavailable (private mode, etc.) -- theme still
      // applies for this session, just won't persist across reloads.
    }
  }, [theme]);

  const toggleTheme = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  return <ThemeContext.Provider value={{ theme, toggleTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside ThemeProvider");
  return ctx;
}
