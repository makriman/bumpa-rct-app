"use client";

import { useState } from "react";
import { Badge, StatePanel } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { useApiResource } from "@/lib/use-api-resource";

type Cadence = "daily" | "weekly";
type Subscription = {
  id: string | null;
  cadence: Cadence;
  status: "off" | "active" | "paused";
  revision: number;
  phone_identity_id: string | null;
  recipient_preview: string | null;
  timezone: string;
  local_hour: number;
  weekday: number;
  effective: boolean;
  delivery_available: boolean;
};
type Settings = {
  subscriptions: Subscription[];
  recipients: { id: string; phone_e164: string; label: string | null }[];
  store_timezone: string;
  notice: string;
};
const weekdays = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

function SubscriptionForm({
  subscription,
  recipients,
  onSaved,
}: {
  subscription: Subscription;
  recipients: Settings["recipients"];
  onSaved: (settings: Settings) => void;
}) {
  const [identity, setIdentity] = useState(
    subscription.phone_identity_id ?? "",
  );
  const [timezone, setTimezone] = useState(subscription.timezone);
  const [hour, setHour] = useState(subscription.local_hour);
  const [weekday, setWeekday] = useState(subscription.weekday);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selected = recipients.find((recipient) => recipient.id === identity);
  const cadence = subscription.cadence;
  const save = async (status: "active" | "paused") => {
    setBusy(true);
    setError("");
    try {
      const result = await apiRequest<Settings>(
        "/settings/proactive-insights",
        {
          method: "PUT",
          body: JSON.stringify({
            cadence,
            status,
            expected_revision: subscription.revision,
            phone_identity_id: identity || null,
            timezone,
            local_hour: hour,
            weekday,
          }),
        },
      );
      onSaved(result);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Your subscription could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="card card-pad"
      aria-label={`${cadence} business summary`}
    >
      <div className="header-actions">
        <h3>{cadence === "daily" ? "Daily summary" : "Weekly summary"}</h3>
        <Badge>
          {subscription.effective
            ? "Active"
            : subscription.status === "active"
              ? "Delivery paused"
              : subscription.status === "paused"
                ? "Paused"
                : "Off"}
        </Badge>
      </div>
      <p>
        {cadence === "daily"
          ? "Previous business-calendar day’s orders."
          : "Previous complete Monday–Sunday week’s orders."}{" "}
        Totals include their exact dates, currency and data coverage.
      </p>
      <div className="field">
        <label htmlFor={`${cadence}-recipient`}>
          Send only to my selected number
        </label>
        <select
          id={`${cadence}-recipient`}
          className="select"
          value={identity}
          disabled={busy}
          onChange={(event) => setIdentity(event.target.value)}
        >
          <option value="">Choose your approved number</option>
          {recipients.map((recipient) => (
            <option key={recipient.id} value={recipient.id}>
              {recipient.label ? `${recipient.label} · ` : ""}
              {recipient.phone_e164}
            </option>
          ))}
        </select>
        {!recipients.length && (
          <p className="field-help">
            No eligible number belongs to your account. An opted-out or removed
            number cannot receive summaries.
          </p>
        )}
      </div>
      <div className="field">
        <label htmlFor={`${cadence}-timezone`}>Delivery timezone</label>
        <input
          id={`${cadence}-timezone`}
          className="input"
          value={timezone}
          disabled={busy}
          maxLength={64}
          placeholder="Africa/Lagos"
          onChange={(event) => setTimezone(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={`${cadence}-hour`}>Send at or after local hour</label>
        <select
          id={`${cadence}-hour`}
          className="select"
          value={hour}
          disabled={busy}
          onChange={(event) => setHour(Number(event.target.value))}
        >
          {Array.from({ length: 24 }, (_, value) => (
            <option key={value} value={value}>
              {String(value).padStart(2, "0")}:00
            </option>
          ))}
        </select>
      </div>
      {cadence === "weekly" && (
        <div className="field">
          <label htmlFor="weekly-weekday">Delivery day</label>
          <select
            id="weekly-weekday"
            className="select"
            value={weekday}
            disabled={busy}
            onChange={(event) => setWeekday(Number(event.target.value))}
          >
            {weekdays.map((day, index) => (
              <option key={day} value={index}>
                {day}
              </option>
            ))}
          </select>
        </div>
      )}
      <p className="field-help">
        {selected
          ? `Recipient preview: ${selected.phone_e164} · ${cadence === "weekly" ? `${weekdays[weekday]} ` : "Daily "}${String(hour).padStart(2, "0")}:00 ${timezone}.`
          : "Choose an exact recipient before subscribing. New numbers are never enrolled automatically."}
      </p>
      {!subscription.delivery_available && (
        <p className="alert alert-info">
          Summary delivery is currently unavailable. You can save or pause your
          preference; this does not activate WhatsApp delivery.
        </p>
      )}
      {error && (
        <p className="alert alert-danger" role="alert">
          {error}
        </p>
      )}
      <div className="header-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={busy || !selected || !timezone.trim()}
          onClick={() => void save("active")}
        >
          {busy
            ? "Saving…"
            : subscription.status === "active"
              ? "Save schedule"
              : `Subscribe ${cadence}`}
        </button>
        {subscription.status === "active" && (
          <button
            type="button"
            className="button button-secondary"
            disabled={busy}
            onClick={() => void save("paused")}
          >
            Pause {cadence}
          </button>
        )}
      </div>
    </section>
  );
}

export function ProactiveSettings() {
  const settings = useApiResource<Settings>("/settings/proactive-insights");
  return (
    <section aria-labelledby="proactive-settings-title">
      <h2 id="proactive-settings-title">Optional business messages</h2>
      <p>
        Choose daily or weekly WhatsApp summaries for your own approved number.
        They are off until you subscribe. Research participation is separate.
      </p>
      {settings.status === "loading" ? (
        <StatePanel type="loading" />
      ) : settings.status === "error" ? (
        <StatePanel
          type="error"
          description={settings.error ?? undefined}
          action={
            <button
              type="button"
              className="button button-secondary"
              onClick={() => void settings.reload()}
            >
              Reload settings
            </button>
          }
        />
      ) : settings.data ? (
        <>
          <p className="field-help">
            Report dates use the business timezone:{" "}
            {settings.data.store_timezone}. Pause here or reply STOP on your
            selected number. START alone does not create a subscription.
          </p>
          <div className="grid">
            {settings.data.subscriptions.map((subscription) => (
              <SubscriptionForm
                key={`${subscription.cadence}:${subscription.revision}`}
                subscription={subscription}
                recipients={settings.data!.recipients}
                onSaved={settings.replace}
              />
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}
