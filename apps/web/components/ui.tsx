"use client";

import Link from "next/link";
import { useEffect, useId, useRef } from "react";
import { statusTone, type Tone } from "@/lib/demo-data";

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="brand" aria-label="Bumpa Bestie home">
      <span className="brand-mark" aria-hidden="true">
        B
      </span>
      {!compact && <span className="brand-word">Bumpa Bestie</span>}
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
          {type === "error" ? "!" : "✦"}
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
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const dialog = dialogRef.current;
    const focusable = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((element) => !element.hasAttribute("hidden"));
    (focusable()[0] ?? dialog)?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) {
        event.preventDefault();
        dialog?.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      window.setTimeout(() => {
        if (previousFocus?.isConnected) previousFocus.focus();
      }, 0);
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(e) => {
        if (e.currentTarget === e.target) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close dialog"
          >
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {actions && <div className="modal-actions">{actions}</div>}
      </div>
    </div>
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
      <span aria-hidden="true">{tone === "warning" ? "!" : "✓"}</span>
      <span>{message}</span>
      <button
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

export function Chart({
  values,
  labels,
  alt = false,
}: {
  values: number[];
  labels: string[];
  alt?: boolean;
}) {
  return (
    <div
      className="chart"
      role="img"
      aria-label={`Bar chart: ${values.map((value, i) => `${labels[i]} ${value}`).join(", ")}`}
    >
      {values.map((value, i) => (
        <div className="chart-col" key={`${labels[i]}-${i}`}>
          <div
            className={`chart-bar ${alt && i % 3 === 2 ? "alt" : ""}`}
            style={{ height: `${value}%` }}
            title={`${labels[i]}: ${value}`}
          />
          <span className="chart-label">{labels[i]}</span>
        </div>
      ))}
    </div>
  );
}

export function DemoStateToggle({
  state,
  setState,
}: {
  state: "ready" | "loading" | "empty" | "error";
  setState: (state: "ready" | "loading" | "empty" | "error") => void;
}) {
  return (
    <select
      className="filter-select"
      aria-label="Preview page state"
      value={state}
      onChange={(e) => setState(e.target.value as typeof state)}
    >
      <option value="ready">Demo: ready</option>
      <option value="loading">Demo: loading</option>
      <option value="empty">Demo: empty</option>
      <option value="error">Demo: error</option>
    </select>
  );
}
