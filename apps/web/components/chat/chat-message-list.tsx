"use client";

import {
  ArrowClockwiseIcon,
  SparkleIcon,
  WarningIcon,
} from "@phosphor-icons/react";
import Image from "next/image";
import type { RefObject } from "react";
import type { ChatState } from "@/lib/chat-state";
import { formatMessageTime } from "@/lib/chat-state";

const suggestions = [
  "Summarise this month",
  "What should I restock?",
  "Which customers should I follow up?",
];

type ChatMessageListProps = {
  endRef: RefObject<HTMLDivElement>;
  loadingOlder: boolean;
  onLoadOlder: () => void;
  onRetryConversation: () => void;
  onRetrySend: () => void;
  onSuggestion: (value: string) => void;
  state: ChatState;
};

export function ChatMessageList({
  endRef,
  loadingOlder,
  onLoadOlder,
  onRetryConversation,
  onRetrySend,
  onSuggestion,
  state,
}: ChatMessageListProps) {
  if (state.phase === "loading") {
    return (
      <div className="bestie-message-scroll" aria-busy="true">
        <div className="bestie-message-column bestie-message-skeletons">
          <span className="sr-only">Loading conversation</span>
          <i />
          <i />
          <i />
        </div>
      </div>
    );
  }

  if (state.loadError) {
    return (
      <div className="bestie-message-scroll">
        <div className="bestie-chat-empty" role="alert">
          <span className="bestie-empty-icon">
            <WarningIcon size={24} />
          </span>
          <h1>This chat could not be opened</h1>
          <p>{state.loadError}</p>
          <button type="button" onClick={onRetryConversation}>
            <ArrowClockwiseIcon size={18} /> Try again
          </button>
        </div>
      </div>
    );
  }

  if (state.messages.length === 0) {
    return (
      <div className="bestie-message-scroll">
        <div className="bestie-chat-empty">
          <span className="bestie-empty-icon">
            <SparkleIcon size={25} weight="fill" />
          </span>
          <h1>What can I help you understand?</h1>
          <p>
            Ask about sales, stock, products, customers, orders, or the next
            decision in your business.
          </p>
          <div className="bestie-prompt-grid" aria-label="Suggested questions">
            {suggestions.map((suggestion) => (
              <button
                type="button"
                key={suggestion}
                onClick={() => onSuggestion(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bestie-message-scroll">
      <div className="bestie-message-column" aria-live="polite">
        {state.olderCursor && (
          <button
            type="button"
            className="bestie-older-messages"
            disabled={loadingOlder}
            onClick={onLoadOlder}
          >
            {loadingOlder
              ? "Loading earlier messages…"
              : "Load earlier messages"}
          </button>
        )}
        {state.messages.map((message) => (
          <article
            className={`bestie-message bestie-message-${message.direction}`}
            key={message.id}
          >
            {message.direction === "outbound" && (
              <Image
                className="bestie-message-avatar"
                src="/brand-mark.svg"
                alt=""
                width={34}
                height={34}
              />
            )}
            <div className="bestie-message-body">
              <div className="bestie-message-text">{message.content}</div>
              <div className="bestie-message-meta">
                <span>
                  {message.direction === "outbound" ? "Bumpa Bestie" : "You"} ·{" "}
                  {formatMessageTime(message.createdAt)}
                </span>
                {message.delivery === "sending" && <span>Sending…</span>}
                {message.delivery === "failed" && <span>Not sent</span>}
              </div>
              {message.source && (
                <div className="bestie-message-source">{message.source}</div>
              )}
            </div>
          </article>
        ))}
        {["sending", "retrying"].includes(state.phase) && (
          <div
            className="bestie-thinking"
            role="status"
            aria-label="Bumpa Bestie is thinking"
          >
            <Image
              className="bestie-message-avatar"
              src="/brand-mark.svg"
              alt=""
              width={34}
              height={34}
            />
            <span className="bestie-thinking-dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
          </div>
        )}
        {state.failedSend?.error && (
          <div className="bestie-send-error" role="alert">
            <WarningIcon size={18} />
            <span>
              <strong>Your message was not sent.</strong>
              {state.failedSend.error}
            </span>
            <button type="button" onClick={onRetrySend}>
              Try again
            </button>
          </div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
