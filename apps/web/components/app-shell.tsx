"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { apiRequest, demoFallbackEnabled } from "@/lib/api";
import {
  adminNav,
  researchNav,
  userNav,
  type NavGroup,
} from "@/lib/navigation";
import { Brand } from "./ui";

type Surface = "user" | "admin" | "research";

type SessionView = {
  user: {
    id: string;
    name: string;
    email: string | null;
    phone_e164: string;
  };
  platform_roles: string[];
  memberships: Array<{
    id: string;
    tenant_id: string;
    role: string;
    status: string;
  }>;
  current_tenant_id: string | null;
};

function navFor(surface: Surface): NavGroup[] {
  return surface === "admin"
    ? adminNav
    : surface === "research"
      ? researchNav
      : userNav;
}

export function AppShell({
  surface,
  title,
  children,
  fullBleed = false,
}: {
  surface: Surface;
  title: string;
  children: React.ReactNode;
  fullBleed?: boolean;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const appMainRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const [dataSource, setDataSource] = useState<"checking" | "live" | "demo">(
    "checking",
  );
  const [session, setSession] = useState<SessionView | null>(null);
  const [logoutPending, setLogoutPending] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  useEffect(() => {
    void apiRequest<SessionView>("/auth/me")
      .then((result) => {
        setSession(result);
        setDataSource("live");
        setReady(true);
      })
      .catch(() => {
        if (demoFallbackEnabled) {
          setDataSource("demo");
          setReady(true);
        } else {
          router.replace(`/login?next=${encodeURIComponent(pathname)}`);
        }
      });
  }, [pathname, router]);
  useEffect(() => {
    if (!menuOpen) return;
    const sidebar = sidebarRef.current;
    const appMain = appMainRef.current;
    const menuButton = menuButtonRef.current;
    const previousOverflow = document.body.style.overflow;
    appMain?.setAttribute("inert", "");
    document.body.style.overflow = "hidden";
    const focusable = Array.from(
      sidebar?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    );
    focusable[0]?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
        return;
      }
      if (event.key !== "Tab" || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      appMain?.removeAttribute("inert");
      document.body.style.overflow = previousOverflow;
      menuButton?.focus();
    };
  }, [menuOpen]);
  const nav = navFor(surface);
  const demoName =
    surface === "admin"
      ? "Demo operator"
      : surface === "research"
        ? "Demo researcher"
        : "Demo SME owner";
  const displayName = session?.user.name ?? demoName;
  const currentMembership = session?.memberships.find(
    (membership) => membership.tenant_id === session.current_tenant_id,
  );
  const displayRole = session
    ? surface === "admin"
      ? session.platform_roles.includes("superadmin")
        ? "Platform superadmin"
        : "Platform operator"
      : surface === "research"
        ? "Researcher · redacted access"
        : `${currentMembership?.role ?? "member"} · current workspace`
    : surface === "admin"
      ? "Demo preview · operator"
      : surface === "research"
        ? "Demo preview · researcher"
        : "Demo preview · owner";
  async function handleLogout() {
    if (logoutPending) return;
    setLogoutPending(true);
    setLogoutError(null);
    try {
      await apiRequest<{ message: string }>("/auth/logout", {
        method: "POST",
      });
      router.replace("/login");
    } catch (error) {
      setLogoutError(
        error instanceof Error
          ? error.message
          : "We could not log you out. Please try again.",
      );
      setLogoutPending(false);
    }
  }
  if (!ready && process.env.NODE_ENV !== "development")
    return (
      <main className="page">
        <div className="skeleton" style={{ height: 400 }} />
      </main>
    );
  return (
    <div className="app-layout">
      {menuOpen && (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          tabIndex={-1}
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        ref={sidebarRef}
        id="workspace-navigation"
        className={`sidebar ${menuOpen ? "open" : ""}`}
        aria-label="Primary navigation"
      >
        <div className="sidebar-head">
          <Brand />
          <button
            className="icon-button sidebar-close"
            aria-label="Close navigation panel"
            onClick={() => setMenuOpen(false)}
          >
            ×
          </button>
        </div>
        {nav.map((group) => (
          <div key={group.label}>
            <div className="nav-label">{group.label}</div>
            <nav className="side-nav" aria-label={`${group.label} navigation`}>
              {group.items.map((item) => {
                const active =
                  item.href === pathname ||
                  (item.href !== `/${surface}` &&
                    pathname.startsWith(`${item.href}/`));
                return (
                  <Link
                    key={item.href}
                    className={`side-link ${active ? "active" : ""}`}
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setMenuOpen(false)}
                  >
                    <span className="nav-icon" aria-hidden="true">
                      {item.icon}
                    </span>
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </div>
        ))}
        <div className="sidebar-bottom">
          <div className="user-chip">
            <span className="avatar">
              {displayName
                .split(" ")
                .map((v) => v[0])
                .slice(0, 2)
                .join("")}
            </span>
            <div className="user-meta">
              <strong>{displayName}</strong>
              <span>{displayRole}</span>
            </div>
          </div>
          {logoutError && (
            <p className="sidebar-error" role="alert">
              {logoutError}
            </p>
          )}
          <button
            className="side-link side-action"
            type="button"
            disabled={logoutPending}
            aria-busy={logoutPending}
            onClick={() => void handleLogout()}
          >
            <span className="nav-icon" aria-hidden="true">
              ↪
            </span>
            {logoutPending ? "Logging out…" : "Log out"}
          </button>
        </div>
      </aside>
      <div ref={appMainRef} className="app-main">
        <header className="topbar">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button
              ref={menuButtonRef}
              className="icon-button mobile-menu-button"
              aria-label="Open navigation"
              aria-controls="workspace-navigation"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              ☰
            </button>
            <span className="topbar-title">{title}</span>
          </div>
          <div className="topbar-actions">
            <span className="environment">
              {dataSource === "live"
                ? "LIVE API"
                : dataSource === "checking"
                  ? "CHECKING API"
                  : "DEMO DATA"}
            </span>
            <button className="icon-button" aria-label="Notifications">
              ◔
            </button>
            <span className="avatar">
              {displayName
                .split(" ")
                .map((v) => v[0])
                .slice(0, 2)
                .join("")}
            </span>
          </div>
        </header>
        <main id="main-content" className={fullBleed ? "" : "page"}>
          {children}
        </main>
      </div>
    </div>
  );
}
