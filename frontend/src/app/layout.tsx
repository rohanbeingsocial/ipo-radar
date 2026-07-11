import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "RHP Analyst — explainable IPO prospectus analysis",
  description:
    "Upload an Indian RHP/DRHP and get structured extraction, valuation vs peers, risk analysis and a fully explainable investment score. Research & education only — not investment advice.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="sticky top-0 z-40 border-b border-edge bg-surface/90 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
            <Link href="/" className="flex items-center gap-2 text-sm font-bold tracking-tight text-ink">
              <span className="flex h-6 w-6 items-center justify-center rounded bg-accent/15 text-accent">◆</span>
              RHP ANALYST
            </Link>
            <nav className="flex items-center gap-5 text-sm text-ink-2">
              <Link href="/" className="hover:text-ink">Upload</Link>
              <Link href="/analyses" className="hover:text-ink">Analyses</Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
        <footer className="mt-10">
          <p className="mx-auto max-w-4xl border-t border-edge px-4 py-5 text-center text-[11px] leading-relaxed text-ink-3">
            ⚠ RHP Analyst is an automated document-analysis tool for research and education. It is not
            investment advice, not a recommendation to subscribe to or avoid any public offer, and not a
            SEBI-registered research service. Scores reflect only information inside the uploaded
            prospectus and stated assumptions. Verify independently before making any decision.
          </p>
        </footer>
      </body>
    </html>
  );
}
