"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { currentUser } from "@/lib/demo-data";
import { apiRequest, demoFallbackEnabled } from "@/lib/api";
import {
  adminNav,
  researchNav,
  userNav,
  type NavGroup,
} from "@/lib/navigation";
import { Brand } from "./ui";

type Surface = "user" | "admin" | "research";

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
  const [ready, setReady] = useState(false);
  const [dataSource, setDataSource] = useState<"checking" | "live" | "demo">(
    "checking",
  );
  useEffect(() => {
    void apiRequest<unknown>("/auth/me")
      .then(() => {
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
  const nav = navFor(surface);
  const displayName =
    surface === "admin"
      ? "Nneka · Operations"
      : surface === "research"
        ? "Dr. Halima Yusuf"
        : currentUser.name;
  const displayRole =
    surface === "admin"
      ? "Platform operator"
      : surface === "research"
        ? "Researcher · Redacted"
        : `${currentUser.tenant} · Owner`;
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
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <Brand />
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
          <Link className="side-link" href="/login">
            <span className="nav-icon">↪</span>Switch workspace
          </Link>
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button
              className="icon-button mobile-menu-button"
              aria-label="Open navigation"
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
