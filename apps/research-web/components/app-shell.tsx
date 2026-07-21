"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { focusableElements, trapTabKey } from "@bumpabestie/web-foundation";
import { apiRequest } from "@/lib/api";
import { surfaceNav } from "@/lib/navigation";
import { AppIcon } from "./app-icon";
import { Brand } from "./ui";

type SessionView = {
  user: { name: string };
  platform_roles: string[];
};

export function AppShell({
  title,
  children,
  fullBleed = false,
}: {
  title: string;
  children: React.ReactNode;
  fullBleed?: boolean;
}) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<SessionView | null>(null);
  const [logoutPending, setLogoutPending] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void apiRequest<SessionView>("/auth/me")
      .then((result) => {
        setSession(result);
        setReady(true);
      })
      .catch(() => {
        window.location.replace(`/login?next=${encodeURIComponent(pathname)}`);
      });
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const restoreFocusTo = menuButtonRef.current;
    const sidebar = sidebarRef.current;
    const content = contentRef.current;
    const previousOverflow = document.body.style.overflow;
    const focusable = focusableElements(sidebar);
    content?.setAttribute("inert", "");
    document.body.style.overflow = "hidden";
    focusable[0]?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
        return;
      }
      trapTabKey(event, sidebar);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      content?.removeAttribute("inert");
      document.body.style.overflow = previousOverflow;
      restoreFocusTo?.focus();
    };
  }, [menuOpen]);

  const navigation = surfaceNav;
  const displayName = session?.user.name ?? "Researcher";
  const displayRole = "Research access";

  const logout = async () => {
    if (logoutPending) return;
    setLogoutPending(true);
    setLogoutError(null);
    try {
      await apiRequest("/auth/logout", { method: "POST" });
      window.location.replace("/login");
    } catch (reason) {
      setLogoutError(
        reason instanceof Error ? reason.message : "We could not log you out.",
      );
      setLogoutPending(false);
    }
  };

  if (!ready) {
    return (
      <main className="page" aria-busy="true">
        <div className="skeleton" style={{ height: 400 }} />
      </main>
    );
  }

  return (
    <div className="app-layout">
      {menuOpen && (
        <button
          type="button"
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
            type="button"
            className="icon-button sidebar-close"
            aria-label="Close navigation panel"
            onClick={() => setMenuOpen(false)}
          >
            <AppIcon name="close" />
          </button>
        </div>
        {navigation.map((group) => (
          <div key={group.label}>
            <div className="nav-label">{group.label}</div>
            <nav className="side-nav" aria-label={`${group.label} navigation`}>
              {group.items.map((item) => {
                const active =
                  item.href === pathname ||
                  pathname.startsWith(`${item.href}/`);
                return (
                  <Link
                    key={item.href}
                    className={`side-link ${active ? "active" : ""}`}
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setMenuOpen(false)}
                  >
                    <span className="nav-icon" aria-hidden="true">
                      <AppIcon name={item.icon} />
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
                .map((part) => part[0])
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
            type="button"
            className="side-link side-action"
            disabled={logoutPending}
            onClick={() => void logout()}
          >
            <span className="nav-icon" aria-hidden="true">
              <AppIcon name="logout" />
            </span>
            {logoutPending ? "Logging out…" : "Log out"}
          </button>
        </div>
      </aside>
      <div ref={contentRef} className="app-main">
        <header className="topbar">
          <div className="topbar-heading">
            <button
              ref={menuButtonRef}
              type="button"
              className="icon-button mobile-menu-button"
              aria-label="Open navigation"
              aria-controls="workspace-navigation"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <AppIcon name="menu" />
            </button>
            <span className="topbar-title">{title}</span>
          </div>
        </header>
        <main id="main-content" className={fullBleed ? "" : "page"}>
          {children}
        </main>
      </div>
    </div>
  );
}
