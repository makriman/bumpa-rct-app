"use client";

import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { Badge } from "@/components/ui";
import { conversations } from "@/lib/demo-data";
import { apiRequest, demoFallbackEnabled } from "@/lib/api";

type Message = {
  from: "user" | "agent";
  text: React.ReactNode;
  time: string;
  source?: string;
};
const initial: Message[] = [
  {
    from: "user",
    text: "What sold best this week, and should I reorder it?",
    time: "10:42",
  },
  {
    from: "agent",
    text: (
      <>
        <strong>Adire Table Runner</strong> was your top product from 5–12 July:
        24 units sold, generating <strong>₦456,000</strong>. That is 41% more
        units than the previous week.
        <br />
        <br />
        You have 9 units left. At the current pace, that is about 2.6 days of
        stock. I would reorder before Tuesday and keep at least 18 units as
        buffer stock.
      </>
    ),
    time: "10:42",
    source: "Bumpa products + orders · Synced 12 minutes ago",
  },
];

const replies: Record<string, string> = {
  "Which customers should I follow up?":
    "Your strongest follow-up opportunity is the group of 18 customers who bought once in the last 90 days but have not returned. Together, their first orders were worth ₦612,000. Start with the six who purchased table linen, since you have a related restock arriving this week.",
  "What is moving slowly?":
    "The Woven Storage Basket is moving slowest: 3 units in 30 days, with 21 currently in stock. Consider bundling it with the Adire Table Runner before discounting it on its own.",
  "Summarise this month":
    "So far this month you have ₦2.84m in sales from 126 orders. Sales are 12% ahead of the same period last month, led by table linen. Gross profit is currently unavailable from Bumpa, so I have not estimated your margin.",
};

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>(initial);
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const [active, setActive] = useState("weekly");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [fallback, setFallback] = useState(true);
  const send = async (text = input) => {
    const clean = text.trim();
    if (!clean || typing) return;
    setMessages((v) => [...v, { from: "user", text: clean, time: "Now" }]);
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
      setFallback(false);
      answer = response.answer;
      source = response.data_freshness
        ? `Live API · data fresh ${new Date(response.data_freshness).toLocaleString()}`
        : "Live API · no freshness timestamp returned";
    } catch (reason) {
      if (!demoFallbackEnabled) {
        answer =
          reason instanceof Error
            ? reason.message
            : "The assistant is unavailable.";
        source = "API error · response not generated";
      } else {
        setFallback(true);
        answer =
          replies[clean] ??
          "This is a labelled demo fallback because the local API is unavailable. Start FastAPI to use tenant-scoped live data.";
        source = "Demo fallback · not live business data";
      }
    }
    setMessages((v) => [
      ...v,
      {
        from: "agent",
        text: answer,
        time: "Now",
        source,
      },
    ]);
    setTyping(false);
  };
  return (
    <AppShell surface="user" title="Bestie chat" fullBleed>
      <div className="chat-layout">
        <aside className="conversation-rail">
          <button
            className="button button-primary"
            style={{ width: "100%" }}
            onClick={() => {
              setMessages([]);
              setActive("new");
            }}
          >
            ＋ New conversation
          </button>
          <div className="conversation-list">
            {conversations.map((c) => (
              <button
                className={`conversation-item ${active === c.id ? "active" : ""}`}
                key={c.id}
                onClick={() => setActive(c.id)}
              >
                <strong>{c.title}</strong>
                <span>
                  {c.preview} · {c.time}
                </span>
              </button>
            ))}
          </div>
        </aside>
        <section className="chat-panel" aria-label="Conversation">
          <div className="chat-context">
            <div>
              <strong>Kaia Home</strong>
              <div className="freshness">
                <strong>● {fallback ? "Demo preview" : "Live API"}</strong>{" "}
                {fallback ? "not tenant data" : "tenant-scoped response"}
              </div>
            </div>
            <Badge tone={fallback ? "warning" : "success"}>
              {fallback ? "Demo fallback" : "API connected"}
            </Badge>
          </div>
          <div className="chat-messages" aria-live="polite">
            {messages.length === 0 ? (
              <div className="empty-state" style={{ margin: "auto" }}>
                <div className="empty-inner">
                  <div className="empty-icon">✦</div>
                  <h2>What is on your mind?</h2>
                  <p>
                    Ask about sales, stock, products, customers, orders, or the
                    next decision in your business.
                  </p>
                </div>
              </div>
            ) : (
              messages.map((message, index) => (
                <div className={`message ${message.from}`} key={index}>
                  {message.from === "agent" && (
                    <span className="avatar">BB</span>
                  )}
                  <div className="message-content">
                    <div
                      className={`bubble ${message.from === "user" ? "bubble-user" : "bubble-agent"}`}
                    >
                      {message.text}
                      {message.source && (
                        <div className="source-card">↻ {message.source}</div>
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
            <div className="suggestions">
              {Object.keys(replies).map((s) => (
                <button className="suggestion" key={s} onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
            <div className="composer">
              <textarea
                aria-label="Message Bumpa Bestie"
                placeholder="Ask about your business…"
                value={input}
                rows={1}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
              />
              <button
                className="send-button"
                aria-label="Send message"
                onClick={() => send()}
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
