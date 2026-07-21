"use client";

import { ArrowUpIcon } from "@phosphor-icons/react";
import { useEffect, useRef } from "react";
import { useHydrated } from "@/lib/use-hydrated";

type ChatComposerProps = {
  disabled: boolean;
  draft: string;
  onDraftChange: (value: string) => void;
  onSend: () => void;
};

export function ChatComposer({
  disabled,
  draft,
  onDraftChange,
  onSend,
}: ChatComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const interactive = useHydrated();

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 176)}px`;
  }, [draft]);

  return (
    <div className="bestie-composer-shell">
      <div className="bestie-composer">
        <label className="sr-only" htmlFor="bestie-message">
          Message Bumpa Bestie
        </label>
        <textarea
          ref={textareaRef}
          id="bestie-message"
          placeholder="Ask about your business"
          rows={1}
          value={draft}
          disabled={!interactive}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (!disabled && draft.trim()) onSend();
            }
          }}
        />
        <button
          type="button"
          className="bestie-send-button"
          aria-label="Send message"
          disabled={!interactive || disabled || !draft.trim()}
          onClick={onSend}
        >
          <ArrowUpIcon size={20} weight="bold" />
        </button>
      </div>
      <p>Bestie can make mistakes. Check important business decisions.</p>
    </div>
  );
}
