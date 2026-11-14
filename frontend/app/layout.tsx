import type { Metadata } from "next";
import AtlasFrame from "./atlas-frame";
import "./globals.css";

export const metadata: Metadata = { title: "Atlas DFE" };

const restoreTheme = `try {
  const theme = localStorage.getItem("nfe-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch {}`;

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR" suppressHydrationWarning>
    <head><script dangerouslySetInnerHTML={{ __html: restoreTheme }} /></head>
    <body><AtlasFrame>{children}</AtlasFrame></body>
  </html>;
}
