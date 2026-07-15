"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, PageHeader, StatePanel } from "@/components/ui";
import { ApiError, apiRequest } from "@/lib/api";
import {
  formatDate,
  titleCase,
  type OnboardingStep,
  type TenantOnboarding,
} from "@/lib/platform-data";

const steps: Array<{ id: OnboardingStep; label: string }> = [
  { id: "owner", label: "Owner" },
  { id: "phone", label: "WhatsApp identity" },
  { id: "bumpa", label: "Bumpa" },
  { id: "initial_sync", label: "Initial data sync" },
  { id: "hermes", label: "Hermes" },
  { id: "review", label: "Review" },
  { id: "completed", label: "Active" },
];

const terminalJobStatuses = new Set([
  "succeeded",
  "failed",
  "dead_letter",
  "cancelled",
]);

type TenantDraft = {
  name: string;
  slug: string;
  business_category: string;
  city: string;
  country: "NG" | "KE";
  timezone: string;
  currency_code: string;
};

type OwnerDraft = {
  name: string;
  phone_e164: string;
  email: string;
};

type BumpaDraft = {
  api_key: string;
  scope_type: "business_id" | "location_id";
  scope_id: string;
  store_timezone: string;
  store_currency: string;
};

type SyncWindow = {
  date_from: string;
  date_to: string;
};

type OnboardingListResponse =
  | TenantOnboarding[]
  | { items: TenantOnboarding[] };

const supportedBusinessMarkets = [
  {
    country: "NG" as const,
    label: "Nigeria",
    timezone: "Africa/Lagos",
    currencyCode: "NGN",
  },
  {
    country: "KE" as const,
    label: "Kenya",
    timezone: "Africa/Nairobi",
    currencyCode: "KES",
  },
] as const;

const currencyCodePattern = /^[A-Z]{3}$/;

function isIanaTimezone(value: string): boolean {
  if (!value) return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format();
    return true;
  } catch {
    return false;
  }
}

function onboardingStartBody(draft: TenantDraft): string {
  return JSON.stringify({
    slug: draft.slug,
    name: draft.name,
    business_category: draft.business_category || null,
    country: draft.country,
    city: draft.city || null,
    timezone: draft.timezone,
    currency_code: draft.currency_code,
  });
}

function randomKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function sessionCommandKey(storageKey: string): string {
  const existing = window.sessionStorage.getItem(storageKey);
  if (existing) return existing;
  const created = randomKey();
  window.sessionStorage.setItem(storageKey, created);
  return created;
}

function clearSessionCommandKey(storageKey: string): void {
  window.sessionStorage.removeItem(storageKey);
}

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    return error.correlationId
      ? `${error.message} (reference ${error.correlationId})`
      : error.message;
  }
  return error instanceof Error
    ? error.message
    : "The onboarding command could not be completed.";
}

function didAdvance(
  before: TenantOnboarding,
  after: TenantOnboarding,
): boolean {
  return (
    after.revision > before.revision ||
    after.current_step !== before.current_step ||
    after.status === "completed"
  );
}

function stepIndex(step: OnboardingStep): number {
  return steps.findIndex((item) => item.id === step);
}

function defaultSyncWindow(): SyncWindow {
  const requestedTo = new Date();
  const requestedFrom = new Date(requestedTo);
  requestedFrom.setUTCDate(requestedFrom.getUTCDate() - 30);
  return {
    date_from: requestedFrom.toISOString().slice(0, 10),
    date_to: requestedTo.toISOString().slice(0, 10),
  };
}

function StepSummary({
  onboarding,
  step,
}: {
  onboarding: TenantOnboarding;
  step: OnboardingStep;
}) {
  if (step === "owner" && onboarding.owner) {
    return (
      <>
        <span>{onboarding.owner.name ?? "Owner"}</span>
        <Badge>{titleCase(onboarding.owner.status)}</Badge>
      </>
    );
  }
  if (step === "phone" && onboarding.phone) {
    return (
      <>
        <span>
          {onboarding.phone.label ?? "Owner"} · {onboarding.phone.phone_masked}
        </span>
        <Badge>{titleCase(onboarding.phone.status)}</Badge>
      </>
    );
  }
  if (step === "bumpa" && onboarding.bumpa) {
    return (
      <>
        <span>
          {titleCase(onboarding.bumpa.scope_type)} · ••••
          {onboarding.bumpa.scope_id_last4}
          {` · ${onboarding.bumpa.store_timezone} · ${onboarding.bumpa.store_currency}`}
        </span>
        <Badge>{titleCase(onboarding.bumpa.status)}</Badge>
      </>
    );
  }
  if (step === "initial_sync" && onboarding.initial_sync) {
    return (
      <>
        <span>
          {titleCase(
            onboarding.initial_sync.completion_quality ??
              onboarding.initial_sync.sync_status ??
              onboarding.initial_sync.job_status,
          )}
        </span>
        <Badge>{titleCase(onboarding.initial_sync.job_status)}</Badge>
      </>
    );
  }
  if (step === "hermes" && onboarding.hermes) {
    return (
      <>
        <span>{onboarding.hermes.profile_name}</span>
        <Badge>{titleCase(onboarding.hermes.status)}</Badge>
      </>
    );
  }
  if (step === "review" || step === "completed") {
    return (
      <>
        <span>{onboarding.tenant.name}</span>
        <Badge>
          {onboarding.status === "completed" ? "Active" : "Ready for review"}
        </Badge>
      </>
    );
  }
  return <span>Saved</span>;
}

function LockedSteps({ onboarding }: { onboarding: TenantOnboarding }) {
  const currentIndex = stepIndex(onboarding.current_step);
  return (
    <div className="grid" aria-label="Saved onboarding steps">
      {steps
        .filter(
          ({ id }, index) =>
            id !== "completed" &&
            (index < currentIndex || onboarding.status === "completed"),
        )
        .map(({ id, label }) => (
          <Card padded key={id}>
            <div className="card-head" style={{ marginBottom: 0 }}>
              <div>
                <span className="eyebrow">Saved · locked</span>
                <h3 style={{ marginTop: 8 }}>{label}</h3>
              </div>
              <div className="detail-row" style={{ border: 0, padding: 0 }}>
                <StepSummary onboarding={onboarding} step={id} />
              </div>
            </div>
          </Card>
        ))}
    </div>
  );
}

export function ResumableOnboardingStart() {
  const router = useRouter();
  const [draft, setDraft] = useState<TenantDraft>({
    name: "",
    slug: "",
    business_category: "",
    city: "",
    country: "NG",
    timezone: "Africa/Lagos",
    currency_code: "NGN",
  });
  const [existing, setExisting] = useState<TenantOnboarding[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const loadExisting = useCallback(async () => {
    try {
      const response =
        await apiRequest<OnboardingListResponse>("/admin/onboardings");
      setExisting(Array.isArray(response) ? response : response.items);
      setError("");
    } catch (reason) {
      setError(messageFor(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadExisting();
  }, [loadExisting]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const storageKey = "bumpa-bestie:onboarding:start";
    const idempotencyKey = sessionCommandKey(storageKey);
    const body = onboardingStartBody(draft);
    setBusy(true);
    setError("");
    try {
      const onboarding = await apiRequest<TenantOnboarding>(
        "/admin/onboardings",
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body,
        },
      );
      clearSessionCommandKey(storageKey);
      router.push(`/admin/onboarding/${onboarding.id}`);
    } catch (reason) {
      // Replaying the same start key is safe after a lost response. A server
      // rejection is definitive and gets a fresh key after the form is fixed.
      if (reason instanceof ApiError && !reason.retryable) {
        clearSessionCommandKey(storageKey);
        setError(messageFor(reason));
        setBusy(false);
        return;
      }
      try {
        const recovered = await apiRequest<TenantOnboarding>(
          "/admin/onboardings",
          {
            method: "POST",
            headers: { "Idempotency-Key": idempotencyKey },
            body,
          },
        );
        clearSessionCommandKey(storageKey);
        router.push(`/admin/onboarding/${recovered.id}`);
      } catch {
        setError(messageFor(reason));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell surface="admin" title="Onboard SME">
      <PageHeader
        title="Onboard a new SME"
        description="Start a durable provisioning record, then resume safely after any interruption."
        actions={
          <Link className="button button-secondary" href="/admin/tenants">
            View tenants
          </Link>
        }
      />
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      <div className="grid onboarding-layout onboarding-start-layout">
        <Card padded>
          <div className="card-head">
            <div>
              <span className="eyebrow">New provisioning record</span>
              <h2>Business</h2>
            </div>
            <Badge>Draft</Badge>
          </div>
          <form onSubmit={(event) => void submit(event)}>
            <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
              <div className="grid grid-2">
                <div className="field">
                  <label htmlFor="business-name">Business name</label>
                  <input
                    id="business-name"
                    className="input"
                    required
                    value={draft.name}
                    onChange={(event) =>
                      setDraft((value) => ({
                        ...value,
                        name: event.target.value,
                      }))
                    }
                  />
                </div>
                <div className="field">
                  <label htmlFor="business-slug">Slug</label>
                  <input
                    id="business-slug"
                    className="input"
                    required
                    pattern="[a-z0-9-]+"
                    value={draft.slug}
                    onChange={(event) =>
                      setDraft((value) => ({
                        ...value,
                        slug: event.target.value
                          .toLowerCase()
                          .replace(/[^a-z0-9-]/g, ""),
                      }))
                    }
                  />
                </div>
                <div className="field">
                  <label htmlFor="business-category">Category</label>
                  <input
                    id="business-category"
                    className="input"
                    value={draft.business_category}
                    onChange={(event) =>
                      setDraft((value) => ({
                        ...value,
                        business_category: event.target.value,
                      }))
                    }
                  />
                </div>
                <div className="field">
                  <label htmlFor="business-city">City</label>
                  <input
                    id="business-city"
                    className="input"
                    value={draft.city}
                    onChange={(event) =>
                      setDraft((value) => ({
                        ...value,
                        city: event.target.value,
                      }))
                    }
                  />
                </div>
                <div className="field">
                  <label htmlFor="business-country">Country</label>
                  <select
                    id="business-country"
                    className="input"
                    required
                    value={draft.country}
                    onChange={(event) => {
                      const market = supportedBusinessMarkets.find(
                        (candidate) => candidate.country === event.target.value,
                      );
                      if (!market) return;
                      setDraft((value) => ({
                        ...value,
                        country: market.country,
                        timezone: market.timezone,
                        currency_code: market.currencyCode,
                      }));
                    }}
                    aria-describedby="business-country-help"
                  >
                    {supportedBusinessMarkets.map((market) => (
                      <option value={market.country} key={market.country}>
                        {market.label} ({market.country})
                      </option>
                    ))}
                  </select>
                  <span className="field-help" id="business-country-help">
                    Sets the reporting defaults; review them below before
                    provisioning.
                  </span>
                </div>
                <div className="field">
                  <label htmlFor="business-timezone">Business timezone</label>
                  <input
                    id="business-timezone"
                    className="input"
                    required
                    autoComplete="off"
                    value={draft.timezone}
                    onChange={(event) =>
                      setDraft((value) => ({
                        ...value,
                        timezone: event.target.value,
                      }))
                    }
                    aria-invalid={
                      draft.timezone.length > 0 &&
                      !isIanaTimezone(draft.timezone)
                    }
                    aria-describedby="business-timezone-help"
                  />
                  <span className="field-help" id="business-timezone-help">
                    IANA timezone for reporting days and scheduled insights.
                  </span>
                </div>
                <div className="field">
                  <label htmlFor="business-currency">Business currency</label>
                  <input
                    id="business-currency"
                    className="input"
                    required
                    maxLength={3}
                    pattern="[A-Z]{3}"
                    title="Enter a three-letter currency code such as NGN or KES"
                    autoComplete="off"
                    spellCheck={false}
                    value={draft.currency_code}
                    onChange={(event) =>
                      setDraft((value) => ({
                        ...value,
                        currency_code: event.target.value.toUpperCase(),
                      }))
                    }
                    aria-describedby="business-currency-help"
                  />
                  <span className="field-help" id="business-currency-help">
                    Three-letter ISO code, for example NGN or KES.
                  </span>
                </div>
              </div>
              <button
                className="button button-primary"
                type="submit"
                disabled={
                  !draft.name ||
                  !draft.slug ||
                  !isIanaTimezone(draft.timezone) ||
                  !currencyCodePattern.test(draft.currency_code) ||
                  busy
                }
                aria-busy={busy}
              >
                {busy ? "Starting…" : "Start onboarding →"}
              </button>
            </fieldset>
          </form>
        </Card>
        <Card padded>
          <div className="card-head">
            <div>
              <span className="eyebrow">Safe to resume</span>
              <h2>Open onboardings</h2>
            </div>
          </div>
          {loading ? (
            <div aria-live="polite">Loading…</div>
          ) : existing.filter((item) => item.status !== "completed").length ? (
            <div className="grid">
              {existing
                .filter((item) => item.status !== "completed")
                .map((item) => (
                  <Link
                    className="card card-pad"
                    href={`/admin/onboarding/${item.id}`}
                    key={item.id}
                  >
                    <strong>{item.tenant.name}</strong>
                    <p style={{ color: "var(--ink-soft)" }}>
                      {titleCase(item.current_step)} · updated{" "}
                      {formatDate(item.updated_at)}
                    </p>
                  </Link>
                ))}
            </div>
          ) : (
            <p style={{ color: "var(--ink-soft)" }}>
              No unfinished onboarding records.
            </p>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

export function ResumableOnboarding({
  onboardingId,
}: {
  onboardingId: string;
}) {
  const [onboarding, setOnboarding] = useState<TenantOnboarding | null>(null);
  const [owner, setOwner] = useState<OwnerDraft>({
    name: "",
    phone_e164: "",
    email: "",
  });
  const [phoneLabel, setPhoneLabel] = useState("Owner");
  const [bumpa, setBumpa] = useState<BumpaDraft>({
    api_key: "",
    scope_type: "business_id",
    scope_id: "",
    store_timezone: "",
    store_currency: "",
  });
  const [syncWindow, setSyncWindow] = useState<SyncWindow>(defaultSyncWindow);
  const bumpaCommandKey = useRef<string | null>(null);
  const bumpaContextSeededFor = useRef<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const value = await apiRequest<TenantOnboarding>(
      `/admin/onboardings/${onboardingId}`,
    );
    setOnboarding(value);
    if (bumpaContextSeededFor.current !== onboardingId) {
      bumpaContextSeededFor.current = onboardingId;
      setBumpa({
        api_key: "",
        scope_type: "business_id",
        scope_id: "",
        store_timezone: value.tenant.timezone,
        store_currency: value.tenant.currency_code,
      });
    }
    return value;
  }, [onboardingId]);

  useEffect(() => {
    let active = true;
    void refresh()
      .then(() => {
        if (active) setError("");
      })
      .catch((reason) => {
        if (active) setError(messageFor(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refresh]);

  useEffect(() => {
    const jobStatus = onboarding?.initial_sync?.job_status;
    if (
      onboarding?.current_step !== "initial_sync" ||
      !jobStatus ||
      terminalJobStatuses.has(jobStatus)
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [onboarding?.current_step, onboarding?.initial_sync?.job_status, refresh]);

  const command = async (
    step: Exclude<OnboardingStep, "completed">,
    path: string,
    body?: Record<string, unknown>,
    options?: { ephemeralKey?: boolean },
  ) => {
    if (!onboarding) return;
    const before = onboarding;
    const storageKey = `bumpa-bestie:onboarding:${onboarding.id}:${step}`;
    const idempotencyKey = options?.ephemeralKey
      ? (bumpaCommandKey.current ??= randomKey())
      : sessionCommandKey(storageKey);
    setBusy(true);
    setError("");
    try {
      const value = await apiRequest<TenantOnboarding>(
        `/admin/onboardings/${onboarding.id}/${path}`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": idempotencyKey,
            "If-Match": String(onboarding.revision),
          },
          body: body ? JSON.stringify(body) : undefined,
        },
      );
      setOnboarding(value);
      if (step === "owner") {
        setOwner({ name: "", phone_e164: "", email: "" });
      }
      if (step === "bumpa") {
        setBumpa({
          api_key: "",
          scope_type: "business_id",
          scope_id: "",
          store_timezone: onboarding.tenant.timezone,
          store_currency: onboarding.tenant.currency_code,
        });
      }
      if (options?.ephemeralKey) bumpaCommandKey.current = null;
      else clearSessionCommandKey(storageKey);
    } catch (reason) {
      try {
        const authoritative = await refresh();
        if (didAdvance(before, authoritative)) {
          if (step === "owner") {
            setOwner({ name: "", phone_e164: "", email: "" });
          }
          if (step === "bumpa") {
            setBumpa({
              api_key: "",
              scope_type: "business_id",
              scope_id: "",
              store_timezone: authoritative.tenant.timezone,
              store_currency: authoritative.tenant.currency_code,
            });
          }
          if (options?.ephemeralKey) bumpaCommandKey.current = null;
          else clearSessionCommandKey(storageKey);
        } else {
          if (reason instanceof ApiError) {
            if (options?.ephemeralKey) {
              // A received API error proves there is no ambiguous response in
              // flight. A replacement write-only credential needs a fresh key
              // because its fingerprint must differ from the rejected input.
              bumpaCommandKey.current = null;
            } else if (!reason.retryable) {
              clearSessionCommandKey(storageKey);
            }
          }
          setError(messageFor(reason));
        }
      } catch {
        setError(messageFor(reason));
      }
    } finally {
      if (step === "bumpa") {
        setBumpa((value) => ({ ...value, api_key: "" }));
      }
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <AppShell surface="admin" title="Onboard SME">
        <PageHeader
          title="Resume onboarding"
          description="Loading the authoritative provisioning record."
        />
        <StatePanel type="loading" />
      </AppShell>
    );
  }

  if (!onboarding) {
    return (
      <AppShell surface="admin" title="Onboard SME">
        <PageHeader
          title="Onboarding unavailable"
          description="The provisioning record could not be loaded."
        />
        <StatePanel
          type="error"
          title="Could not load onboarding"
          description={error || "Confirm the record exists and try again."}
          action={
            <button
              className="button button-primary"
              onClick={() => {
                setLoading(true);
                void refresh()
                  .catch((reason) => setError(messageFor(reason)))
                  .finally(() => setLoading(false));
              }}
            >
              Retry
            </button>
          }
        />
      </AppShell>
    );
  }

  const current = steps.find((step) => step.id === onboarding.current_step);
  const initialSyncSucceeded =
    onboarding.initial_sync?.job_status === "succeeded" &&
    ((onboarding.initial_sync.sync_status === "success" &&
      onboarding.initial_sync.completion_quality === "complete") ||
      (onboarding.initial_sync.sync_status === "partial" &&
        onboarding.initial_sync.completion_quality === "accepted_partial" &&
        onboarding.initial_sync.orders_availability === "available"));
  const initialSyncTerminal = onboarding.initial_sync
    ? terminalJobStatuses.has(onboarding.initial_sync.job_status)
    : false;

  return (
    <AppShell surface="admin" title="Onboard SME">
      <PageHeader
        title={onboarding.tenant.name}
        description="Every completed step is locked and sourced from the durable server record."
        actions={
          <Link className="button button-secondary" href="/admin/onboarding">
            All onboardings
          </Link>
        }
      />
      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div className="timeline" aria-label="Onboarding progress">
          {steps.map((step, index) => {
            const currentIndex = stepIndex(onboarding.current_step);
            const complete =
              index < currentIndex || onboarding.status === "completed";
            return (
              <div
                className="timeline-item"
                key={step.id}
                aria-current={
                  step.id === onboarding.current_step ? "step" : undefined
                }
              >
                <strong
                  style={{
                    color:
                      step.id === onboarding.current_step
                        ? "var(--forest)"
                        : undefined,
                  }}
                >
                  {step.label}
                </strong>
                <p>
                  {complete
                    ? "Complete"
                    : step.id === onboarding.current_step
                      ? "In progress"
                      : "Not started"}
                </p>
              </div>
            );
          })}
        </div>
      </div>

      {onboarding.status === "attention_required" && onboarding.failure && (
        <div className="alert alert-danger" role="alert">
          {titleCase(onboarding.failure.step)} needs attention:{" "}
          {titleCase(onboarding.failure.code)}. The saved record is safe to
          retry.
        </div>
      )}
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}

      <div className="grid onboarding-layout onboarding-progress-layout">
        <LockedSteps onboarding={onboarding} />
        <Card padded>
          <div className="card-head">
            <div>
              <span className="eyebrow">
                Revision {onboarding.revision} · {titleCase(onboarding.status)}
              </span>
              <h2>{current?.label ?? "Onboarding"}</h2>
            </div>
            <Badge>
              {onboarding.status === "completed"
                ? "Active"
                : onboarding.current_step === "review"
                  ? "Ready for review"
                  : "Provisioning"}
            </Badge>
          </div>

          {onboarding.current_step === "owner" && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void command("owner", "owner", {
                  name: owner.name,
                  phone_e164: owner.phone_e164,
                  email: owner.email || null,
                });
              }}
            >
              <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
                <div className="grid grid-2">
                  <div className="field">
                    <label htmlFor="owner-name">Owner name</label>
                    <input
                      id="owner-name"
                      className="input"
                      required
                      value={owner.name}
                      onChange={(event) =>
                        setOwner((value) => ({
                          ...value,
                          name: event.target.value,
                        }))
                      }
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="owner-phone">Phone in E.164 format</label>
                    <input
                      id="owner-phone"
                      className="input"
                      type="tel"
                      required
                      placeholder="+234…"
                      value={owner.phone_e164}
                      onChange={(event) =>
                        setOwner((value) => ({
                          ...value,
                          phone_e164: event.target.value,
                        }))
                      }
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="owner-email">Email (optional)</label>
                    <input
                      id="owner-email"
                      className="input"
                      type="email"
                      value={owner.email}
                      onChange={(event) =>
                        setOwner((value) => ({
                          ...value,
                          email: event.target.value,
                        }))
                      }
                    />
                  </div>
                </div>
                <button
                  className="button button-primary"
                  type="submit"
                  disabled={busy || !owner.name || !owner.phone_e164}
                  aria-busy={busy}
                >
                  {busy ? "Saving…" : "Save owner →"}
                </button>
              </fieldset>
            </form>
          )}

          {onboarding.current_step === "phone" && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void command("phone", "phone", {
                  confirmation: "approve",
                  label: phoneLabel,
                });
              }}
            >
              <div className="field">
                <label htmlFor="phone-label">WhatsApp identity label</label>
                <input
                  id="phone-label"
                  className="input"
                  required
                  disabled={busy}
                  value={phoneLabel}
                  onChange={(event) => setPhoneLabel(event.target.value)}
                />
              </div>
              <div className="alert alert-info">
                This maps the owner&apos;s approved WhatsApp number to this
                tenant. Platform-admin roles are independent, so an operator may
                also own a demo tenant.
              </div>
              <button
                className="button button-primary"
                type="submit"
                disabled={busy || !phoneLabel}
                aria-busy={busy}
              >
                {busy ? "Saving…" : "Approve identity →"}
              </button>
            </form>
          )}

          {onboarding.current_step === "bumpa" && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void command(
                  "bumpa",
                  "bumpa",
                  {
                    api_key: bumpa.api_key,
                    scope_type: bumpa.scope_type,
                    scope_id: bumpa.scope_id,
                    store_timezone: bumpa.store_timezone,
                    store_currency: bumpa.store_currency,
                  },
                  { ephemeralKey: true },
                );
              }}
            >
              <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
                <div className="grid grid-2">
                  <div className="field">
                    <label htmlFor="bumpa-api-key">Bumpa API key</label>
                    <input
                      id="bumpa-api-key"
                      className="input"
                      type="password"
                      required
                      autoComplete="off"
                      autoCapitalize="none"
                      spellCheck={false}
                      value={bumpa.api_key}
                      onChange={(event) =>
                        setBumpa((value) => ({
                          ...value,
                          api_key: event.target.value,
                        }))
                      }
                      aria-describedby="bumpa-key-help"
                    />
                    <span className="field-help" id="bumpa-key-help">
                      Write only. It is kept in memory for this attempt and
                      cleared after every response.
                    </span>
                  </div>
                  <div className="field">
                    <label htmlFor="bumpa-scope-type">Account scope</label>
                    <select
                      id="bumpa-scope-type"
                      className="select"
                      value={bumpa.scope_type}
                      onChange={(event) =>
                        setBumpa((value) => ({
                          ...value,
                          scope_type: event.target
                            .value as BumpaDraft["scope_type"],
                        }))
                      }
                    >
                      <option value="business_id">Business</option>
                      <option value="location_id">Location</option>
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="bumpa-scope-id">
                      {bumpa.scope_type === "business_id"
                        ? "Business ID"
                        : "Location ID"}
                    </label>
                    <input
                      id="bumpa-scope-id"
                      className="input"
                      required
                      autoComplete="off"
                      value={bumpa.scope_id}
                      onChange={(event) =>
                        setBumpa((value) => ({
                          ...value,
                          scope_id: event.target.value,
                        }))
                      }
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="bumpa-store-timezone">Store timezone</label>
                    <input
                      id="bumpa-store-timezone"
                      className="input"
                      required
                      autoComplete="off"
                      value={bumpa.store_timezone}
                      onChange={(event) =>
                        setBumpa((value) => ({
                          ...value,
                          store_timezone: event.target.value,
                        }))
                      }
                      aria-invalid={
                        bumpa.store_timezone.length > 0 &&
                        !isIanaTimezone(bumpa.store_timezone)
                      }
                      aria-describedby="bumpa-timezone-help"
                    />
                    <span className="field-help" id="bumpa-timezone-help">
                      IANA name used to validate Bumpa’s local-day reporting
                      window, for example Africa/Lagos.
                    </span>
                  </div>
                  <div className="field">
                    <label htmlFor="bumpa-store-currency">Store currency</label>
                    <input
                      id="bumpa-store-currency"
                      className="input"
                      required
                      maxLength={3}
                      pattern="[A-Z]{3}"
                      title="Enter a three-letter currency code such as NGN or KES"
                      autoComplete="off"
                      spellCheck={false}
                      value={bumpa.store_currency}
                      onChange={(event) =>
                        setBumpa((value) => ({
                          ...value,
                          store_currency: event.target.value.toUpperCase(),
                        }))
                      }
                      aria-describedby="bumpa-currency-help"
                    />
                    <span className="field-help" id="bumpa-currency-help">
                      Three-letter code used to reject ambiguous money, for
                      example NGN or KES.
                    </span>
                  </div>
                </div>
                <button
                  className="button button-primary"
                  type="submit"
                  disabled={
                    busy ||
                    !bumpa.api_key ||
                    !bumpa.scope_id ||
                    !isIanaTimezone(bumpa.store_timezone) ||
                    !currencyCodePattern.test(bumpa.store_currency)
                  }
                  aria-busy={busy}
                >
                  {busy ? "Verifying…" : "Connect and verify Bumpa →"}
                </button>
              </fieldset>
            </form>
          )}

          {onboarding.current_step === "initial_sync" && (
            <div aria-live="polite" aria-busy={busy}>
              {onboarding.initial_sync ? (
                <>
                  <div className="detail-row">
                    <span className="detail-value">Queue job</span>
                    <Badge>
                      {titleCase(onboarding.initial_sync.job_status)}
                    </Badge>
                  </div>
                  <div className="detail-row">
                    <span className="detail-value">Sync run</span>
                    <Badge>
                      {titleCase(onboarding.initial_sync.sync_status)}
                    </Badge>
                  </div>
                  <div className="detail-row">
                    <span className="detail-value">Completion quality</span>
                    <Badge>
                      {titleCase(onboarding.initial_sync.completion_quality)}
                    </Badge>
                  </div>
                  <div className="detail-row">
                    <span className="detail-value">Requested window</span>
                    <span>
                      {onboarding.initial_sync.requested_from} to{" "}
                      {onboarding.initial_sync.requested_to}
                    </span>
                  </div>
                  <div className="detail-row">
                    <span className="detail-value">Orders imported</span>
                    <span>
                      {onboarding.initial_sync.orders_count ?? "Unavailable"} ·{" "}
                      {titleCase(onboarding.initial_sync.orders_availability)}
                    </span>
                  </div>
                  <p style={{ color: "var(--ink-soft)", lineHeight: 1.6 }}>
                    Hermes remains locked until the server validates a
                    successful initial data load.
                  </p>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <button
                      className="button button-secondary"
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void refresh().catch((reason) =>
                          setError(messageFor(reason)),
                        )
                      }
                    >
                      Refresh sync status
                    </button>
                    <button
                      className="button button-primary"
                      type="button"
                      disabled={busy || !initialSyncSucceeded}
                      onClick={() =>
                        void command("initial_sync", "initial-sync/accept", {
                          confirmation: "accept",
                        })
                      }
                    >
                      {busy ? "Validating…" : "Validate sync and continue →"}
                    </button>
                    {initialSyncTerminal && !initialSyncSucceeded && (
                      <button
                        className="button button-primary"
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void command("initial_sync", "initial-sync", {
                            date_from: onboarding.initial_sync?.requested_from,
                            date_to: onboarding.initial_sync?.requested_to,
                          })
                        }
                      >
                        {busy ? "Retrying sync…" : "Retry initial sync →"}
                      </button>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <p style={{ color: "var(--ink-soft)", lineHeight: 1.6 }}>
                    Queue the first strict Bumpa import. Onboarding cannot reach
                    Hermes or activation until the run succeeds and is
                    validated.
                  </p>
                  <div className="grid grid-2">
                    <div className="field">
                      <label htmlFor="sync-date-from">Import from</label>
                      <input
                        id="sync-date-from"
                        className="input"
                        type="date"
                        required
                        disabled={busy}
                        max={syncWindow.date_to}
                        value={syncWindow.date_from}
                        onChange={(event) =>
                          setSyncWindow((value) => ({
                            ...value,
                            date_from: event.target.value,
                          }))
                        }
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="sync-date-to">Import through</label>
                      <input
                        id="sync-date-to"
                        className="input"
                        type="date"
                        required
                        disabled={busy}
                        min={syncWindow.date_from}
                        value={syncWindow.date_to}
                        onChange={(event) =>
                          setSyncWindow((value) => ({
                            ...value,
                            date_to: event.target.value,
                          }))
                        }
                      />
                    </div>
                  </div>
                  <button
                    className="button button-primary"
                    type="button"
                    disabled={busy}
                    aria-busy={busy}
                    onClick={() =>
                      void command("initial_sync", "initial-sync", syncWindow)
                    }
                  >
                    {busy ? "Starting sync…" : "Start required initial sync →"}
                  </button>
                </>
              )}
            </div>
          )}

          {onboarding.current_step === "hermes" && (
            <div aria-busy={busy}>
              <p style={{ color: "var(--ink-soft)", lineHeight: 1.6 }}>
                Provision an isolated Hermes profile only after the initial
                Bumpa data load. Credentials and internal runtime addresses
                never reach this browser.
              </p>
              <button
                className="button button-primary"
                type="button"
                disabled={busy}
                aria-busy={busy}
                onClick={() =>
                  void command("hermes", "hermes", {
                    confirmation: "provision",
                  })
                }
              >
                {busy ? "Provisioning…" : "Provision and verify Hermes →"}
              </button>
            </div>
          )}

          {onboarding.current_step === "review" && (
            <div>
              <div className="alert alert-success">
                All required resources report ready. Activating performs one
                final server-side reconciliation and records the completion
                audit.
              </div>
              {steps.slice(0, 5).map(({ id, label }) => (
                <div className="detail-row" key={id}>
                  <span className="detail-value">{label}</span>
                  <StepSummary onboarding={onboarding} step={id} />
                </div>
              ))}
              <button
                className="button button-primary"
                type="button"
                disabled={busy}
                aria-busy={busy}
                onClick={() =>
                  void command("review", "complete", {
                    confirmation: "activate",
                  })
                }
              >
                {busy ? "Activating…" : "Activate tenant →"}
              </button>
            </div>
          )}

          {onboarding.current_step === "completed" && (
            <div>
              <div className="alert alert-success" role="status">
                Tenant active. Onboarding completed{" "}
                {formatDate(onboarding.completed_at)}.
              </div>
              <Link
                className="button button-primary"
                href={`/admin/tenants/${onboarding.tenant_id}`}
              >
                Open tenant
              </Link>
            </div>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
