// Dark by default; the light theme is the same tokens stepped for a light surface (kept in localStorage, wrapped:
// private windows may refuse it).
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

type Theme = "dark" | "light";
const Ctx = createContext<{ theme: Theme; setTheme: (t: Theme) => void }>({ theme: "dark", setTheme: () => {} });

function initial(): Theme {
  try {
    return localStorage.getItem("sl-theme") === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(initial);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem("sl-theme", theme);
    } catch {
      /* not persisted */
    }
  }, [theme]);
  return <Ctx.Provider value={{ theme, setTheme }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);
