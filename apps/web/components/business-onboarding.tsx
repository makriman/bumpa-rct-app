"use client";

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "@/lib/api";

type Answer = string | string[] | { value: string | string[]; detail: string };
type Question = {
  key: string;
  label: string;
  title: string;
  help: string;
  placeholder?: string;
  kind: "text" | "single" | "multi";
  options?: string[];
};
export type BusinessProfile = {
  business_name: string;
  can_edit: boolean;
  status: "not_started" | "in_progress" | "review" | "completed" | "paused";
  step: string;
  revision: number;
  answers: Record<string, Answer>;
  draft: Record<string, Answer>;
  questions: Question[];
  order_hint: { value: string; note: string } | null;
  suggestions: string[];
  pending_update?: { changes: Record<string, Answer> };
};

type Command = {
  action: string;
  field?: string;
  value?: string | string[];
  detail?: string;
};

function readable(value: Answer | undefined): string {
  if (!value) return "Not shared yet";
  if (typeof value === "object" && !Array.isArray(value))
    return `${readable(value.value)}: ${value.detail}`;
  return Array.isArray(value) ? value.join(", ") : value;
}

type OnboardingProps = {
  onAsk?: (question: string) => void;
  onSkip?: () => void;
  profilePage?: boolean;
};

export function useBusinessOnboarding(enabled = true) {
  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [justSaved, setJustSaved] = useState(false);
  const saving = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProfile(
        await apiRequest<BusinessProfile>("/settings/business-profile"),
      );
    } catch {
      setError(
        "I couldn’t load your business profile. Check your connection and try again.",
      );
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    if (enabled) void load();
  }, [enabled, load]);

  const act = async (command: Command) => {
    if (!profile || saving.current) return false;
    saving.current = true;
    setBusy(true);
    setError(null);
    try {
      const next = await apiRequest<BusinessProfile>(
        "/settings/business-profile",
        {
          method: "POST",
          body: JSON.stringify({
            ...command,
            expected_revision: profile.revision,
          }),
        },
      );
      setProfile(next);
      if (command.action === "complete") setJustSaved(true);
      return true;
    } catch (reason) {
      const message =
        reason instanceof Error
          ? reason.message
          : "Your answer couldn’t be saved. Please try again.";
      if (
        message.includes("another chat") ||
        message.includes("question has changed")
      ) {
        try {
          setProfile(
            await apiRequest<BusinessProfile>("/settings/business-profile"),
          );
        } catch {
          /* Keep the current answer available for retry. */
        }
      }
      setError(message);
      return false;
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };

  return { profile, error, busy, loading, justSaved, load, act };
}

export type BusinessOnboardingController = ReturnType<
  typeof useBusinessOnboarding
>;

export function BusinessOnboarding(props: OnboardingProps) {
  const controller = useBusinessOnboarding();
  return <BusinessOnboardingView {...props} controller={controller} />;
}

export function BusinessOnboardingView({
  controller,
  onAsk,
  onSkip,
  profilePage = false,
}: OnboardingProps & { controller: BusinessOnboardingController }) {
  const { profile, error, busy, loading, justSaved, load } = controller;
  const heading = useRef<HTMLHeadingElement>(null);
  const currentStep = profile?.step;
  const currentStatus = profile?.status;
  useEffect(() => {
    if (currentStatus && currentStatus !== "not_started")
      heading.current?.focus();
  }, [currentStep, currentStatus]);
  const act = async (command: Command) => {
    const succeeded = await controller.act(command);
    if (succeeded && command.action === "pause") onSkip?.();
  };

  const shell = (children: React.ReactNode) => (
    <section
      className={`business-onboarding ${profilePage ? "onboarding-profile" : ""}`}
      aria-label="Your business with Bestie"
      aria-busy={busy || loading}
    >
      <div className="onboarding-brand">
        <Image src="/brand-mark.svg" width={36} height={36} alt="" />
        <span>
          BUMPA BESTIE{" "}
          <span className="onboarding-brand-note">Made for your business</span>
        </span>
      </div>
      {children}
      {error && (
        <div className="alert alert-danger onboarding-error" role="alert">
          {error}
        </div>
      )}
    </section>
  );

  if (loading)
    return shell(<p role="status">Getting your business profile ready…</p>);
  if (!profile)
    return shell(
      <>
        <h2>Let’s get to know your business</h2>
        <button
          type="button"
          className="button button-primary"
          onClick={() => void load()}
        >
          Try again
        </button>
        {onSkip && (
          <button
            type="button"
            className="button button-ghost"
            onClick={onSkip}
          >
            Ask Bestie now
          </button>
        )}
      </>,
    );

  const editing =
    profile.status === "in_progress" || profile.status === "review";
  if (!profile.can_edit) {
    return profilePage
      ? shell(
          <>
            <h2>What Bestie knows about this business</h2>
            <ProfileAnswers profile={profile} answers={profile.answers} />
            <p className="onboarding-note">
              The business owner or an admin can update these details.
            </p>
          </>,
        )
      : shell(
          <>
            <h1>What shall we work on today?</h1>
            <p>
              Ask what’s on your mind, or start with a question for your
              business.
            </p>
            {profile.suggestions.map((question) => (
              <button
                type="button"
                className="onboarding-suggestion"
                key={question}
                onClick={() => onAsk?.(question)}
              >
                {question}
                <span aria-hidden="true">↗</span>
              </button>
            ))}
          </>,
        );
  }
  if (!editing && profile.status !== "completed")
    return shell(
      <>
        <span className="onboarding-eyebrow">YOUR BUSINESS. YOUR BESTIE.</span>
        <h1 ref={heading} tabIndex={-1}>
          {profile.status === "paused"
            ? "Let’s pick up where we left off."
            : "Big plans start with\na little hello."}
        </h1>
        <p className="onboarding-intro">
          Hi 👋🏽 I’m BumpaBestie, your AI business assistant. I’m here to help
          you make better decisions and grow your business.
        </p>
        <p>
          Tell me a little about <strong>{profile.business_name}</strong> so my
          advice fits what you sell, your customers, and what matters to you.
        </p>
        <div className="onboarding-promise">
          <span>7 short questions</span>
          <span>One at a time</span>
          <span>At your pace</span>
        </div>
        <div className="onboarding-actions">
          <button
            type="button"
            className="button button-primary"
            disabled={busy}
            onClick={() => void act({ action: "start" })}
          >
            {busy
              ? "Getting ready…"
              : profile.status === "paused"
                ? "Continue my setup →"
                : "Let’s start →"}
          </button>
          {onSkip && (
            <button
              type="button"
              className="button button-ghost"
              disabled={busy}
              onClick={() => void act({ action: "pause" })}
            >
              Ask me later
            </button>
          )}
        </div>
        <p className="onboarding-note">
          English or Pidgin is fine. No business jargon needed. Your answers
          stay in a saved draft until you review them. Your reviewed profile is
          shared with your business team to help Bestie tailor advice. This does
          not change your research consent.
        </p>
      </>,
    );

  if (!editing && profile.status === "completed")
    return shell(
      <SavedBusinessProfile
        profile={profile}
        profilePage={profilePage}
        justSaved={justSaved}
        busy={busy}
        heading={heading}
        act={act}
        onAsk={onAsk}
        onSkip={onSkip}
      />,
    );

  const index = profile.questions.findIndex((q) => q.key === profile.step);
  const review = profile.status === "review";
  return shell(
    <>
      <div className="onboarding-progress-label">
        <span>
          {review
            ? "A quick check together"
            : `Question ${index + 1} of ${profile.questions.length}`}
        </span>
        <span>
          {review ? "All seven answered" : "Getting to know your business"}
        </span>
      </div>
      <div
        className="onboarding-progress"
        role="progressbar"
        aria-label="Business setup progress"
        aria-valuemin={0}
        aria-valuemax={7}
        aria-valuenow={review ? 7 : index}
      >
        <span style={{ width: `${(review ? 1 : index / 7) * 100}%` }} />
      </div>
      {review ? (
        <>
          <h1 ref={heading} tabIndex={-1}>
            Does this sound like your business?
          </h1>
          <p>
            Thanks — I have a much better picture now. Check your answers before
            I remember them.
          </p>
          <ProfileAnswers
            profile={profile}
            answers={profile.draft}
            onEdit={(field) => void act({ action: "edit", field })}
            busy={busy}
          />
          <div className="onboarding-actions">
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() => void act({ action: "complete" })}
            >
              {busy
                ? "Saving your profile…"
                : "Yes, save my business profile →"}
            </button>
          </div>
        </>
      ) : (
        <QuestionForm
          key={`${profile.step}-${profile.revision}`}
          question={profile.questions[index]}
          answer={profile.draft[profile.step]}
          hint={profile.step === "monthly_orders" ? profile.order_hint : null}
          busy={busy}
          headingRef={heading}
          onSubmit={(value, detail) =>
            void act({ action: "answer", field: profile.step, value, detail })
          }
        />
      )}
      <div className="onboarding-footer">
        <button
          type="button"
          className="button button-ghost"
          disabled={busy || index === 0}
          onClick={() => void act({ action: "back" })}
        >
          ← Back
        </button>
        <span>Saved each time you continue</span>
        <button
          type="button"
          className="button button-ghost"
          disabled={busy}
          onClick={() => void act({ action: "pause" })}
        >
          Finish later
        </button>
      </div>
    </>,
  );
}

function SavedBusinessProfile({
  profile,
  profilePage,
  justSaved,
  busy,
  heading,
  act,
  onAsk,
  onSkip,
}: OnboardingProps & {
  profile: BusinessProfile;
  justSaved: boolean;
  busy: boolean;
  heading: React.RefObject<HTMLHeadingElement>;
  act: (command: Command) => Promise<void>;
}) {
  const labels = new Map(profile.questions.map((q) => [q.key, q.label]));
  return (
    <>
      <span className="onboarding-eyebrow">
        {justSaved ? "WE’RE READY WHEN YOU ARE" : "YOUR BUSINESS, REMEMBERED"}
      </span>
      <h1 ref={heading} tabIndex={-1}>
        {profilePage
          ? "What Bestie knows about your business"
          : justSaved
            ? "I’ve got you, Bestie."
            : "What shall we work on today?"}
      </h1>
      <p>
        {justSaved
          ? "Your business profile is saved. I’ll keep it in mind whenever we talk, here or on WhatsApp."
          : "Advice that starts with your business, your customers, and your priorities."}
      </p>
      {profilePage && (
        <ProfileAnswers
          profile={profile}
          answers={profile.answers}
          onEdit={(field) => void act({ action: "edit", field })}
          busy={busy}
        />
      )}
      {profile.pending_update && (
        <div className="onboarding-pending">
          <h2>A business update to review</h2>
          <p>
            {Object.entries(profile.pending_update.changes)
              .map(
                ([key, value]) =>
                  `${labels.get(key) ?? key}: ${readable(value)}`,
              )
              .join(" · ")}
          </p>
          <button
            type="button"
            className="button button-primary"
            disabled={busy}
            onClick={() => void act({ action: "confirm_update" })}
          >
            Save this update
          </button>
          <button
            type="button"
            className="button button-ghost"
            disabled={busy}
            onClick={() => void act({ action: "reject_update" })}
          >
            Keep my current details
          </button>
        </div>
      )}
      {onAsk && (
        <div className="onboarding-next">
          <h2>How can I help you grow today?</h2>
          <p>Ask what’s on your mind, or start here.</p>
          {profile.suggestions.map((question) => (
            <button
              type="button"
              className="onboarding-suggestion"
              key={question}
              onClick={() => onAsk(question)}
            >
              {question}
              <span aria-hidden="true">↗</span>
            </button>
          ))}
        </div>
      )}
      <div className="onboarding-actions">
        {!profilePage && (
          <button
            type="button"
            className="button button-ghost"
            disabled={busy}
            onClick={() =>
              void act({ action: "edit", field: profile.questions[0].key })
            }
          >
            Update my business details
          </button>
        )}
        {onSkip && (
          <button
            type="button"
            className="button button-secondary"
            onClick={onSkip}
          >
            Ask my own question →
          </button>
        )}
      </div>
      <p className="onboarding-note">
        Things change. Tell me in chat or update your answers here. I’ll ask
        before saving a business change from our conversation.
      </p>
    </>
  );
}

function ProfileAnswers({
  profile,
  answers,
  onEdit,
  busy,
}: {
  profile: BusinessProfile;
  answers: Record<string, Answer>;
  onEdit?: (field: string) => void;
  busy?: boolean;
}) {
  return (
    <dl className="onboarding-summary">
      {profile.questions.map((q) => (
        <div key={q.key}>
          <dt>{q.label}</dt>
          <dd>
            <span>{readable(answers[q.key])}</span>
            {onEdit && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onEdit(q.key)}
                aria-label={`Change ${q.label.toLowerCase()}`}
              >
                Change
              </button>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function QuestionForm({
  question,
  answer,
  hint,
  busy,
  headingRef,
  onSubmit,
}: {
  question: Question;
  answer?: Answer;
  hint: BusinessProfile["order_hint"];
  busy: boolean;
  headingRef: React.RefObject<HTMLHeadingElement>;
  onSubmit: (value: string | string[], detail?: string) => void;
}) {
  const initial =
    typeof answer === "object" && !Array.isArray(answer)
      ? answer.value
      : answer;
  const [value, setValue] = useState<string | string[]>(
    initial ?? (question.kind === "multi" ? [] : ""),
  );
  const [detail, setDetail] = useState(
    typeof answer === "object" && !Array.isArray(answer) ? answer.detail : "",
  );
  const selected = new Set(Array.isArray(value) ? value : [value]);
  const custom = Array.isArray(value)
    ? value.includes("Other")
    : value === "Something else";
  const valid =
    (Array.isArray(value)
      ? value.length > 0
      : question.kind === "text"
        ? value.trim().length >= 2
        : !!value) &&
    (!custom || detail.trim().length >= 2);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (valid && !busy) onSubmit(value, custom ? detail.trim() : undefined);
      }}
    >
      <h1 ref={headingRef} tabIndex={-1} id="onboarding-question">
        {question.title}
      </h1>
      <p id="onboarding-help">{question.help}</p>
      {hint && (
        <div className="onboarding-hint">
          <span>FROM YOUR BUMPA RECORDS</span>
          <p>{hint.note}</p>
          <button
            type="button"
            disabled={busy}
            onClick={() => setValue(hint.value)}
          >
            Use {hint.value} orders
          </button>
        </div>
      )}
      {question.kind === "text" ? (
        <textarea
          aria-labelledby="onboarding-question"
          aria-describedby="onboarding-help"
          className="input onboarding-textarea"
          placeholder={question.placeholder}
          value={value as string}
          onChange={(e) => setValue(e.target.value)}
          maxLength={1000}
          rows={4}
          disabled={busy}
          required
          minLength={2}
        />
      ) : (
        <fieldset
          className={`onboarding-options ${question.kind === "multi" ? "onboarding-options-multi" : ""}`}
          aria-describedby="onboarding-help"
        >
          <legend className="sr-only">{question.title}</legend>
          {question.options?.map((option) => {
            const checked = selected.has(option);
            return (
              <label
                className={`onboarding-option ${checked ? "is-selected" : ""}`}
                key={option}
              >
                <input
                  type={question.kind === "multi" ? "checkbox" : "radio"}
                  name={question.key}
                  value={option}
                  checked={checked}
                  disabled={busy}
                  onChange={() =>
                    setValue(
                      question.kind === "multi"
                        ? checked
                          ? (value as string[]).filter((v) => v !== option)
                          : [...(value as string[]), option]
                        : option,
                    )
                  }
                />
                <span>{option}</span>
                <span className="onboarding-check" aria-hidden="true">
                  {checked ? "✓" : ""}
                </span>
              </label>
            );
          })}
        </fieldset>
      )}
      {custom && (
        <label className="field onboarding-custom">
          <span>Tell me a little more</span>
          <input
            className="input"
            value={detail}
            onChange={(e) => setDetail(e.target.value)}
            placeholder={question.placeholder}
            maxLength={500}
            minLength={2}
            disabled={busy}
            required
          />
        </label>
      )}
      <div className="onboarding-actions">
        <button
          type="submit"
          className="button button-primary"
          disabled={busy || !valid}
        >
          {busy ? "Saving…" : "Continue →"}
        </button>
        {question.kind === "multi" && (
          <span className="onboarding-note">You can choose more than one.</span>
        )}
      </div>
    </form>
  );
}
