import { describe, expect, it } from "vitest";
import {
  chatReducer,
  groupConversations,
  initialChatState,
} from "@/lib/chat-state";

describe("chat state", () => {
  it("keeps a failed message retryable with the same client message id", () => {
    const sending = chatReducer(initialChatState, {
      type: "SEND_STARTED",
      userMessageId: "pending-1",
      text: "What should I restock?",
      clientMessageId: "client-1",
      requestId: "send-1",
    });
    const failed = chatReducer(sending, {
      type: "SEND_FAILED",
      requestId: "send-1",
      error: "Network unavailable",
    });
    expect(failed.failedSend).toMatchObject({
      text: "What should I restock?",
      clientMessageId: "client-1",
      error: "Network unavailable",
    });
    expect(failed.messages[0].delivery).toBe("failed");

    const retrying = chatReducer(failed, {
      type: "RETRY_STARTED",
      requestId: "retry-1",
    });
    expect(retrying.failedSend?.clientMessageId).toBe("client-1");
    expect(retrying.phase).toBe("retrying");
    expect(retrying.activeSendRequestId).toBe("retry-1");
    expect(retrying.messages).toHaveLength(1);
    expect(retrying.messages[0].delivery).toBe("sending");
  });

  it("replaces optimistic state with a saved response", () => {
    const sending = chatReducer(initialChatState, {
      type: "SEND_STARTED",
      userMessageId: "pending-2",
      text: "Summarise this month",
      clientMessageId: "client-2",
      requestId: "send-2",
    });
    const ready = chatReducer(sending, {
      type: "SEND_SUCCEEDED",
      requestId: "send-2",
      response: {
        answer: "Sales increased this month.",
        conversation_id: "conversation-2",
        inbound_message_id: "inbound-2",
        outbound_message_id: "outbound-2",
        data_freshness: null,
      },
      source: "Workspace data",
    });
    expect(ready.conversationId).toBe("conversation-2");
    expect(ready.phase).toBe("ready");
    expect(ready.messages.map((message) => message.delivery)).toEqual([
      "saved",
      "saved",
    ]);
    expect(ready.messages[1].source).toBe("Workspace data");
  });

  it("preserves a draft while loading a selected conversation", () => {
    const drafting = chatReducer(initialChatState, {
      type: "DRAFT_CHANGED",
      draft: "Compare this with last month",
    });
    const loading = chatReducer(drafting, {
      type: "LOAD_STARTED",
      conversationId: "conversation-1",
      requestId: "load-1",
    });

    expect(loading.draft).toBe("Compare this with last month");
    expect(loading.phase).toBe("loading");
  });

  it("ignores stale conversation responses after the selection changes", () => {
    const firstLoad = chatReducer(initialChatState, {
      type: "LOAD_STARTED",
      conversationId: "conversation-1",
      requestId: "load-1",
    });
    const secondLoad = chatReducer(firstLoad, {
      type: "LOAD_STARTED",
      conversationId: "conversation-2",
      requestId: "load-2",
    });
    const staleResult = chatReducer(secondLoad, {
      type: "LOAD_SUCCEEDED",
      conversationId: "conversation-1",
      requestId: "load-1",
      messages: [
        {
          id: "stale-message",
          direction: "outbound",
          content: "Stale answer",
          createdAt: "2026-07-21T08:00:00Z",
          delivery: "saved",
        },
      ],
      olderCursor: null,
    });

    expect(staleResult).toBe(secondLoad);
    expect(staleResult.conversationId).toBe("conversation-2");
    expect(staleResult.messages).toEqual([]);
  });

  it("ignores stale send responses after a conversation load begins", () => {
    const sending = chatReducer(initialChatState, {
      type: "SEND_STARTED",
      userMessageId: "pending-3",
      text: "What changed?",
      clientMessageId: "client-3",
      requestId: "send-3",
    });
    const loading = chatReducer(sending, {
      type: "LOAD_STARTED",
      conversationId: "conversation-3",
      requestId: "load-3",
    });
    const staleResult = chatReducer(loading, {
      type: "SEND_SUCCEEDED",
      requestId: "send-3",
      response: {
        answer: "A late answer",
        conversation_id: "conversation-late",
        inbound_message_id: "inbound-late",
        outbound_message_id: "outbound-late",
        data_freshness: null,
      },
      source: "Workspace data",
    });

    expect(staleResult).toBe(loading);
    expect(staleResult.conversationId).toBe("conversation-3");
  });

  it("does not prepend older messages to a different conversation", () => {
    const loading = chatReducer(initialChatState, {
      type: "LOAD_STARTED",
      conversationId: "conversation-current",
      requestId: "load-current",
    });
    const ready = chatReducer(loading, {
      type: "LOAD_SUCCEEDED",
      conversationId: "conversation-current",
      requestId: "load-current",
      messages: [],
      olderCursor: null,
    });
    const stalePage = chatReducer(ready, {
      type: "OLDER_MESSAGES_LOADED",
      conversationId: "conversation-previous",
      messages: [
        {
          id: "old-message",
          direction: "inbound",
          content: "Old message",
          createdAt: "2026-07-20T08:00:00Z",
          delivery: "saved",
        },
      ],
      olderCursor: null,
    });

    expect(stalePage).toBe(ready);
  });

  it("groups recent conversations into predictable date sections", () => {
    const now = new Date("2026-07-21T12:00:00Z");
    const groups = groupConversations(
      [
        {
          id: "today",
          title: "Today",
          channel: "web",
          updated_at: "2026-07-21T08:00:00Z",
          last_message_preview: null,
        },
        {
          id: "yesterday",
          title: "Yesterday",
          channel: "web",
          updated_at: "2026-07-20T08:00:00Z",
          last_message_preview: null,
        },
        {
          id: "week",
          title: "Week",
          channel: "web",
          updated_at: "2026-07-17T08:00:00Z",
          last_message_preview: null,
        },
        {
          id: "older",
          title: "Older",
          channel: "web",
          updated_at: "2026-06-01T08:00:00Z",
          last_message_preview: null,
        },
      ],
      now,
    );
    expect(groups.map((group) => group.label)).toEqual([
      "Today",
      "Yesterday",
      "Previous 7 days",
      "Older",
    ]);
  });
});
