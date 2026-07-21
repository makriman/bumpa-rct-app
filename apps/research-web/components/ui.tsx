"use client";

import Link from "next/link";
import { ChatsTeardrop } from "@phosphor-icons/react";
import { useEffect, useId, useRef } from "react";
import { focusableElements } from "@bumpabestie/web-foundation";
import { statusTone, type Tone } from "@bumpabestie/web-foundation";
import { AppIcon } from "./app-icon";

export function Brand({
  compact = false,
  className = "",
}: {
  compact?: boolean;
  className?: string;
}) {
  return (
    <Link
      href="/"
      className={`brand ${className}`.trim()}
      aria-label="Bumpa Bestie home"
    >
      <span className="brand-mark" aria-hidden="true">
        <ChatsTeardrop
          size={25}
          weight="bold"
          aria-hidden="true"
          focusable="false"
        />
      </span>
      {!compact && (
        <span className="brand-word">
          <span className="brand-name">Bumpa</span>
          <span className="brand-bestie">Bestie</span>
        </span>
      )}
    </Link>
  );
}

export function Badge({
  children,
  tone,
}: {
  children: React.ReactNode;
  tone?: Tone;
}) {
  const resolved = tone ?? statusTone(String(children));
  return <span className={`badge badge-${resolved}`}>{children}</span>;
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="page-header">
      <div className="page-title">
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="header-actions">{actions}</div>}
    </div>
  );
}

export function Card({
  children,
  className = "",
  padded = false,
}: {
  children: React.ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section className={`card ${padded ? "card-pad" : ""} ${className}`}>
      {children}
    </section>
  );
}

export function ScrollableTable({
  children,
  label,
  className = "",
  style,
}: {
  children: React.ReactNode;
  label: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className={`table-wrap ${className}`.trim()}
      role="region"
      aria-label={label}
      tabIndex={0}
      style={style}
    >
      {children}
    </div>
  );
}

export function Metric({
  label,
  value,
  trend,
  note,
  bars,
}: {
  label: string;
  value: string;
  trend?: string;
  note?: string;
  bars?: number[];
}) {
  return (
    <Card className="metric">
      <div className="metric-label">
        <span>{label}</span>
        {trend && (
          <span className={trend.startsWith("+") ? "trend-up" : "trend-down"}>
            {trend}
          </span>
        )}
      </div>
      <div className="metric-value">{value}</div>
      {note && <div className="metric-note">{note}</div>}
      {bars && (
        <div className="sparkline" aria-label={`${label} trend`}>
          {bars.map((height, index) => (
            <span key={index} style={{ height: `${height}%` }} />
          ))}
        </div>
      )}
    </Card>
  );
}

export function StatePanel({
  type,
  title,
  description,
  action,
}: {
  type: "empty" | "error" | "loading";
  title?: string;
  description?: string;
  action?: React.ReactNode;
}) {
  if (type === "loading")
    return (
      <Card padded>
        <div aria-live="polite" aria-busy="true">
          <span className="sr-only">Loading content</span>
          {[72, 45, 88, 62].map((width, i) => (
            <div
              key={i}
              className="skeleton"
              style={{
                height: i === 0 ? 24 : 14,
                width: `${width}%`,
                marginBottom: 16,
              }}
            />
          ))}
        </div>
      </Card>
    );
  return (
    <Card className="empty-state">
      <div className="empty-inner">
        <div className="empty-icon" aria-hidden="true">
          <AppIcon name={type === "error" ? "alert" : "sparkles"} size={22} />
        </div>
        <h2>
          {title ??
            (type === "error" ? "Something went wrong" : "Nothing here yet")}
        </h2>
        <p>
          {description ??
            (type === "error"
              ? "We could not load this content. Please try again."
              : "New items will appear here when they are available.")}
        </p>
        {action}
      </div>
    </Card>
  );
}

export function Modal({
  title,
  children,
  onClose,
  actions,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  actions?: React.ReactNode;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    focusableElements(dialog)[0]?.focus();
    return () => {
      if (typeof dialog.close === "function" && dialog.open) dialog.close();
      else dialog.removeAttribute("open");
      window.setTimeout(() => {
        if (previousFocus?.isConnected) previousFocus.focus();
      }, 0);
    };
  }, []);
  return (
    <dialog
      ref={dialogRef}
      className="modal"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onCloseRef.current();
      }}
    >
      <div className="modal-head">
        <h2 id={titleId}>{title}</h2>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          aria-label="Close dialog"
        >
          <AppIcon name="close" />
        </button>
      </div>
      <div className="modal-body">{children}</div>
      {actions && <div className="modal-actions">{actions}</div>}
    </dialog>
  );
}

export function Toast({
  message,
  onClose,
  tone = "success",
}: {
  message: string;
  onClose: () => void;
  tone?: "success" | "warning";
}) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 3600);
    return () => window.clearTimeout(timer);
  }, [onClose]);
  return (
    <div className={`toast toast-${tone}`} role="status">
      <AppIcon name={tone === "warning" ? "alert" : "check"} />
      <span>{message}</span>
      <button
        type="button"
        className="button button-ghost button-small"
        style={{ color: "white" }}
        onClick={onClose}
      >
        Close
      </button>
    </div>
  );
}

export function Filters({
  search,
  setSearch,
  children,
}: {
  search: string;
  setSearch: (value: string) => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="card filters">
      <label className="search">
        <span className="sr-only">Search</span>
        <input
          className="input"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search…"
        />
      </label>
      {children}
    </div>
  );
}
