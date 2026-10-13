import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { MotionProvider } from "@/components/motion-provider";
import { ToastProvider } from "@/components/legacy-ui";

/**
 * Fonts are the project's single font-loading system: Inter for UI and
 * JetBrains Mono for data (the design-system convention), exposed as
 * --font-sans / --font-mono and consumed by the Tailwind fontFamily config.
 * `dark` on <html> activates the LocalLead dark theme (darkMode: "class").
 */
const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains-mono" });

export const metadata: Metadata = {
  title: "LocalLead Intelligence",
  description:
    "Find businesses by industry and location with verified public contact intelligence.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen font-sans antialiased">
        <ToastProvider>
          <MotionProvider>{children}</MotionProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
