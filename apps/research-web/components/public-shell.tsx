import Link from "next/link";
import { Brand } from "./ui";

export function PublicShell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header>
        <nav className="public-nav" aria-label="Research navigation">
          <Brand />
          <Link className="button button-primary button-small" href="/login">
            Research sign in
          </Link>
        </nav>
      </header>
      <main id="main-content">{children}</main>
      <footer className="site-footer">
        <div>
          <Brand />
          <p>Consent-aware, privacy-preserving research.</p>
        </div>
      </footer>
    </>
  );
}
