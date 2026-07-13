"use client";

import { useCallback, useEffect, useState } from "react";
import { AppIcon } from "@/components/app-icon";
import { AppShell } from "@/components/app-shell";
import { Badge } from "@/components/ui";
import { apiRequest, isDemoMode } from "@/lib/api";
import { conversations as demoConversations } from "@/lib/demo-data";

type Message = {
  id: string;
  from: "user" | "agent";
  text: React.ReactNode;
  time: string;
  source?: string;
};

type ConversationSummary = {
  id: string;
  channel: string;
  title: string | null;
  status: string;
  updated_at: string;
};

type ConversationDetail = {
  id: string;
  messages: Array<{
    id: string;
    direction: "inbound" | "outbound";
    content: string;
    created_at: string;
  }>;
};

type DataStatus = "checking" | "live" | "demo" | "error";

const demoInitialMessages: Message[] = [
  {
    id: "demo-question",
    from: "user",
    text: "What sold best this week, and should I reorder it?",
    time: "10:42",
  },
  {
    id: "demo-answer",
    from: "agent",
    text: (
      <>
        <strong>Adire Table Runner</strong> was your top product from 5–12 July:
        24 units sold, generating <strong>₦456,000</strong>. This sample answer
        illustrates how Bestie can explain a restock decision once business data
        is connected.
      </>
    ),
    time: "10:42",
    source: "Demo scenario · not tenant business data",
  },
];

const demoReplies: Record<string, string> = {
  "Which customers should I follow up?":
    "Demo scenario: a useful follow-up segment could be customers who bought once in the last 90 days but have not returned.",
  "What is moving slowly?":
    "Demo scenario: Bestie could compare recent sales velocity with current stock and identify products that may need a bundle or promotion.",
  "Summarise this month":
    "Demo scenario: Bestie could summarise sales, orders, product mix, and important changes once tenant data is connected.",
};

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Updated recently";
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function demoSummaries(): ConversationSummary[] {
  return demoConversations.map((conversation) => ({
    id: conversation.id,
    channel: "demo",
    title: conversation.title,
    status: conversation.preview,
    updated_at: conversation.time,
  }));
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>(
    isDemoMode ? demoInitialMessages : [],
  );
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const [active, setActive] = useState<string | null>(
    isDemoMode ? "weekly" : null,
  );
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>(
    isDemoMode ? demoSummaries() : [],
  );
  const [dataStatus, setDataStatus] = useState<DataStatus>(
    isDemoMode ? "demo" : "checking",
  );
  const [railError, setRailError] = useState<string | null>(null);
  const [conversationError, setConversationError] = useState<string | null>(
    null,
  );
  const [loadingConversation, setLoadingConversation] = useState(false);

  const loadConversations = useCallback(async () => {
    if (isDemoMode) return;
    setRailError(null);
    setDataStatus("checking");
    try {
      const result = await apiRequest<ConversationSummary[]>(
        "/chat/conversations",
      );
      setConversations(result);
      setDataStatus("live");
    } catch (reason) {
      setConversations([]);
      setRailError(
        reason instanceof Error
          ? reason.message
          : "Conversation history is unavailable.",
      );
      setDataStatus("error");
    }
  }, []);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  const selectConversation = async (conversation: ConversationSummary) => {
    setActive(conversation.id);
    setConversationError(null);
    if (isDemoMode) {
      setConversationId(null);
      setMessages(
        conversation.id === "weekly"
          ? demoInitialMessages
          : [
              {
                id: `demo-${conversation.id}`,
                from: "agent",
                text: "This is a demo conversation placeholder. No tenant messages were loaded.",
                time: "Preview",
                source: "Demo history · not tenant business data",
              },
            ],
      );
      return;
    }

    setLoadingConversation(true);
    setMessages([]);
    setConversationId(conversation.id);
    try {
      const result = await apiRequest<ConversationDetail>(
        `/chat/conversations/${encodeURIComponent(conversation.id)}`,
      );
      setMessages(
        result.messages.map((message) => ({
          id: message.id,
          from: message.direction === "inbound" ? "user" : "agent",
          text: message.content,
          time: formatTimestamp(message.created_at),
          source:
            message.direction === "outbound"
              ? "Saved tenant conversation · API"
              : undefined,
        })),
      );
    } catch (reason) {
      setConversationError(
        reason instanceof Error
          ? reason.message
          : "This conversation could not be loaded.",
      );
    } finally {
      setLoadingConversation(false);
    }
  };

  const startConversation = () => {
    setMessages([]);
    setActive(null);
    setConversationId(null);
    setConversationError(null);
  };

  const send = async (text = input) => {
    const clean = text.trim();
    if (!clean || typing) return;
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), from: "user", text: clean, time: "Now" },
    ]);
    setInput("");
    setTyping(true);
    let answer: string;
    let source: string;
    try {
      const response = await apiRequest<{
        answer: string;
        conversation_id: string;
        data_freshness?: string | null;
      }>("/chat/web", {
        method: "POST",
        body: JSON.stringify({
          message: clean,
          conversation_id: conversationId,
          client_message_id: crypto.randomUUID(),
        }),
      });
      setConversationId(response.conversation_id);
      setActive(response.conversation_id);
      answer = response.answer;
      source = response.data_freshness
        ? `Tenant API · data fresh ${new Date(response.data_freshness).toLocaleString()}`
        : "Tenant API · no freshness timestamp returned";
      if (!isDemoMode) void loadConversations();
    } catch (reason) {
      if (isDemoMode) {
        answer =
          demoReplies[clean] ??
          "This demo response is illustrative only. Connect the assistant and tenant data to receive a business-specific answer.";
        source = "Demo response · not live business data";
      } else {
        answer =
          reason instanceof Error
            ? `The assistant is unavailable: ${reason.message}`
            : "The assistant is unavailable right now.";
        source = "API error · no assistant response was generated";
      }
    } finally {
      setTyping(false);
    }
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        from: "agent",
        text: answer,
        time: "Now",
        source,
      },
    ]);
  };

  const contextLabel =
    dataStatus === "demo"
      ? "Kaia Home · demo workspace"
      : "Current tenant workspace";
  const contextDescription =
    dataStatus === "demo"
      ? "Illustrative content — not tenant data"
      : dataStatus === "live"
        ? "Conversation history loaded from the tenant API"
        : dataStatus === "checking"
          ? "Checking the tenant API…"
          : "Conversation API unavailable";
  const badge =
    dataStatus === "demo"
      ? { tone: "warning" as const, label: "Demo preview" }
      : dataStatus === "live"
        ? { tone: "success" as const, label: "Tenant API" }
        : dataStatus === "error"
          ? { tone: "danger" as const, label: "API unavailable" }
          : { tone: "neutral" as const, label: "Connecting" };

  return (
    <AppShell surface="user" title="Bestie chat" fullBleed>
      <div className="chat-layout">
        <aside className="conversation-rail" aria-label="Conversation history">
          <button
            className="button button-primary"
            style={{ width: "100%" }}
            onClick={startConversation}
          >
            ＋ New conversation
          </button>
          {dataStatus === "demo" && (
            <div className="alert alert-warning" style={{ marginTop: 14 }}>
              Demo history only. These examples are not connected to a tenant.
            </div>
          )}
          {dataStatus === "checking" && (
            <div
              aria-label="Loading conversations"
              aria-busy="true"
              style={{ marginTop: 20 }}
            >
              {[80, 62, 73].map((width) => (
                <div
                  className="skeleton"
                  key={width}
                  style={{ height: 56, width: `${width}%`, marginBottom: 10 }}
                />
              ))}
            </div>
          )}
          {railError && (
            <div className="alert alert-danger" style={{ marginTop: 14 }}>
              <div>
                <strong>Conversation history unavailable</strong>
                <div>{railError}</div>
                <button
                  className="button button-secondary button-small"
                  style={{ marginTop: 10 }}
                  onClick={() => void loadConversations()}
                >
                  Try again
                </button>
              </div>
            </div>
          )}
          {dataStatus === "live" && conversations.length === 0 && (
            <p className="field-help" style={{ padding: "14px 4px" }}>
              No saved conversations yet. Start one to see it here.
            </p>
          )}
          <div className="conversation-list">
            {conversations.map((conversation) => (
              <button
                className={`conversation-item ${active === conversation.id ? "active" : ""}`}
                key={conversation.id}
                onClick={() => void selectConversation(conversation)}
              >
                <strong>{conversation.title || "Untitled conversation"}</strong>
                <span>
                  {dataStatus === "demo"
                    ? `${conversation.status} · ${conversation.updated_at}`
                    : `${conversation.channel} · ${formatTimestamp(conversation.updated_at)}`}
                </span>
              </button>
            ))}
          </div>
        </aside>
        <section className="chat-panel" aria-label="Conversation">
          <div className="chat-context">
            <div>
              <strong>{contextLabel}</strong>
              <div className="freshness">{contextDescription}</div>
            </div>
            <Badge tone={badge.tone}>{badge.label}</Badge>
          </div>
          <div className="chat-messages" aria-live="polite">
            {loadingConversation ? (
              <div aria-busy="true" style={{ width: "100%" }}>
                <span className="sr-only">Loading conversation messages</span>
                {[72, 48, 64].map((width) => (
                  <div
                    className="skeleton"
                    key={width}
                    style={{ height: 64, width: `${width}%`, marginBottom: 18 }}
                  />
                ))}
              </div>
            ) : conversationError ? (
              <div className="empty-state" style={{ margin: "auto" }}>
                <div className="empty-inner">
                  <div className="empty-icon">!</div>
                  <h2>Conversation unavailable</h2>
                  <p>{conversationError}</p>
                </div>
              </div>
            ) : messages.length === 0 ? (
              <div className="empty-state" style={{ margin: "auto" }}>
                <div className="empty-inner">
                  <div className="empty-icon">
                    <AppIcon name="sparkles" size={22} />
                  </div>
                  <h2>Start a conversation</h2>
                  <p>
                    Ask about sales, stock, products, customers, orders, or the
                    next decision in your business.
                  </p>
                </div>
              </div>
            ) : (
              messages.map((message) => (
                <div className={`message ${message.from}`} key={message.id}>
                  {message.from === "agent" && (
                    <span className="avatar">BB</span>
                  )}
                  <div className="message-content">
                    <div
                      className={`bubble ${message.from === "user" ? "bubble-user" : "bubble-agent"}`}
                    >
                      {message.text}
                      {message.source && (
                        <div className="source-card">
                          <AppIcon name="refresh" size={13} /> {message.source}
                        </div>
                      )}
                    </div>
                    <div className="message-meta">
                      {message.from === "agent" ? "Bumpa Bestie" : "You"} ·{" "}
                      {message.time}
                    </div>
                  </div>
                </div>
              ))
            )}
            {typing && (
              <div className="message">
                <span className="avatar">BB</span>
                <div
                  className="bubble bubble-agent typing"
                  aria-label="Bumpa Bestie is typing"
                >
                  <i />
                  <i />
                  <i />
                </div>
              </div>
            )}
          </div>
          <div className="composer-wrap">
            {isDemoMode && (
              <div className="suggestions" aria-label="Demo prompts">
                {Object.keys(demoReplies).map((suggestion) => (
                  <button
                    className="suggestion"
                    key={suggestion}
                    onClick={() => void send(suggestion)}
                  >
                    Demo · {suggestion}
                  </button>
                ))}
              </div>
            )}
            <div className="composer">
              <textarea
                aria-label="Message Bumpa Bestie"
                placeholder="Ask about your business…"
                value={input}
                rows={1}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
              />
              <button
                className="send-button"
                aria-label="Send message"
                onClick={() => void send()}
                disabled={!input.trim() || typing}
              >
                ↑
              </button>
            </div>
            <div
              className="field-help"
              style={{ textAlign: "center", marginTop: 8 }}
            >
              Bestie can make mistakes. Verify important business decisions.
            </div>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
