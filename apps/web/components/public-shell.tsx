import Link from "next/link";
import { Brand } from "./ui";

export function PublicShell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header>
        <nav className="public-nav" aria-label="Main navigation">
          <Brand />
          <div className="public-links">
            <Link href="/#how-it-works">How it works</Link>
            <Link href="/about">About</Link>
            <Link href="/research-consent">Research</Link>
            <Link className="button button-primary button-small" href="/login">
              Sign in
            </Link>
          </div>
          <Link
            className="button button-primary button-small mobile-nav"
            href="/login"
          >
            Sign in
          </Link>
        </nav>
      </header>
      <main id="main-content">{children}</main>
      <footer className="site-footer">
        <div>
          <Brand />
          <p>Practical business clarity, right when you need it.</p>
          <p>© 2026 Bumpa Bestie</p>
        </div>
        <div className="footer-links">
          <Link href="/about">About</Link>
          <Link href="/research-consent">Research consent</Link>
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
        </div>
      </footer>
    </>
  );
}
