import type { components } from "@bumpabestie/web-foundation";

export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationSummaryPage =
  components["schemas"]["ConversationSummaryPage"];
export type ChatMessageView = components["schemas"]["ChatMessageView"];
export type ChatMessagePage = components["schemas"]["ChatMessagePage"];
export type ChatResponse = components["schemas"]["ChatResponse"];

export type ChatMessageItem = {
  id: string;
  direction: "inbound" | "outbound";
  content: string;
  createdAt: string;
  delivery: "saved" | "sending" | "failed";
  source?: string;
};

export type FailedSend = {
  userMessageId: string;
  text: string;
  clientMessageId: string;
  error: string;
};

export type ChatState = {
  conversationId: string | null;
  messages: ChatMessageItem[];
  draft: string;
  phase: "empty" | "loading" | "ready" | "sending" | "failed" | "retrying";
  loadError: string | null;
  failedSend: FailedSend | null;
  olderCursor: string | null;
  activeLoadRequestId: string | null;
  activeSendRequestId: string | null;
};

export type ChatAction =
  | { type: "RESET" }
  | { type: "DRAFT_CHANGED"; draft: string }
  | {
      type: "LOAD_STARTED";
      conversationId: string;
      requestId: string;
    }
  | {
      type: "LOAD_SUCCEEDED";
      conversationId: string;
      requestId: string;
      messages: ChatMessageItem[];
      olderCursor: string | null;
    }
  | {
      type: "LOAD_FAILED";
      conversationId: string;
      requestId: string;
      error: string;
    }
  | {
      type: "OLDER_MESSAGES_LOADED";
      conversationId: string;
      messages: ChatMessageItem[];
      olderCursor: string | null;
    }
  | {
      type: "SEND_STARTED";
      userMessageId: string;
      text: string;
      clientMessageId: string;
      requestId: string;
    }
  | { type: "RETRY_STARTED"; requestId: string }
  | {
      type: "SEND_SUCCEEDED";
      requestId: string;
      response: ChatResponse;
      source: string;
    }
  | { type: "SEND_FAILED"; requestId: string; error: string };

export const initialChatState: ChatState = {
  conversationId: null,
  messages: [],
  draft: "",
  phase: "empty",
  loadError: null,
  failedSend: null,
  olderCursor: null,
  activeLoadRequestId: null,
  activeSendRequestId: null,
};

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "RESET":
      return initialChatState;
    case "DRAFT_CHANGED":
      return { ...state, draft: action.draft };
    case "LOAD_STARTED":
      return {
        ...initialChatState,
        draft: state.draft,
        conversationId: action.conversationId,
        phase: "loading",
        activeLoadRequestId: action.requestId,
      };
    case "LOAD_SUCCEEDED": {
      if (
        state.activeLoadRequestId !== action.requestId ||
        state.conversationId !== action.conversationId
      ) {
        return state;
      }
      return {
        ...state,
        conversationId: action.conversationId,
        messages: action.messages,
        phase: action.messages.length ? "ready" : "empty",
        loadError: null,
        olderCursor: action.olderCursor,
        activeLoadRequestId: null,
      };
    }
    case "LOAD_FAILED": {
      if (
        state.activeLoadRequestId !== action.requestId ||
        state.conversationId !== action.conversationId
      ) {
        return state;
      }
      return {
        ...state,
        conversationId: action.conversationId,
        messages: [],
        phase: "failed",
        loadError: action.error,
        activeLoadRequestId: null,
      };
    }
    case "OLDER_MESSAGES_LOADED":
      if (state.conversationId !== action.conversationId) return state;
      return {
        ...state,
        messages: [...action.messages, ...state.messages],
        olderCursor: action.olderCursor,
      };
    case "SEND_STARTED":
      return {
        ...state,
        draft: "",
        phase: "sending",
        loadError: null,
        activeLoadRequestId: null,
        activeSendRequestId: action.requestId,
        failedSend: {
          userMessageId: action.userMessageId,
          text: action.text,
          clientMessageId: action.clientMessageId,
          error: "",
        },
        messages: [
          ...state.messages,
          {
            id: action.userMessageId,
            direction: "inbound",
            content: action.text,
            createdAt: new Date().toISOString(),
            delivery: "sending",
          },
        ],
      };
    case "RETRY_STARTED":
      if (!state.failedSend) return state;
      return {
        ...state,
        phase: "retrying",
        loadError: null,
        activeSendRequestId: action.requestId,
        messages: state.messages.map((message) =>
          message.id === state.failedSend?.userMessageId
            ? { ...message, delivery: "sending" }
            : message,
        ),
      };
    case "SEND_SUCCEEDED":
      if (state.activeSendRequestId !== action.requestId) return state;
      return {
        ...state,
        conversationId: action.response.conversation_id,
        phase: "ready",
        failedSend: null,
        activeSendRequestId: null,
        messages: [
          ...state.messages.map((message) =>
            message.id === state.failedSend?.userMessageId
              ? { ...message, delivery: "saved" as const }
              : message,
          ),
          {
            id: action.response.outbound_message_id,
            direction: "outbound",
            content: action.response.answer,
            createdAt: new Date().toISOString(),
            delivery: "saved",
            source: action.source,
          },
        ],
      };
    case "SEND_FAILED":
      if (state.activeSendRequestId !== action.requestId) return state;
      return {
        ...state,
        phase: "failed",
        activeSendRequestId: null,
        failedSend: state.failedSend
          ? { ...state.failedSend, error: action.error }
          : null,
        messages: state.messages.map((message) =>
          message.id === state.failedSend?.userMessageId
            ? { ...message, delivery: "failed" }
            : message,
        ),
      };
  }
}

const messageTimeFormatter = new Intl.DateTimeFormat(undefined, {
  hour: "numeric",
  minute: "2-digit",
});

export function formatMessageTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Recently"
    : messageTimeFormatter.format(date);
}

export type ConversationGroup = {
  label: "Today" | "Yesterday" | "Previous 7 days" | "Older";
  items: ConversationSummary[];
};

export function groupConversations(
  conversations: ConversationSummary[],
  now = new Date(),
): ConversationGroup[] {
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  );
  const dayInMilliseconds = 86_400_000;
  const groups = new Map<ConversationGroup["label"], ConversationSummary[]>([
    ["Today", []],
    ["Yesterday", []],
    ["Previous 7 days", []],
    ["Older", []],
  ]);
  for (const conversation of conversations) {
    const updatedAt = new Date(conversation.updated_at);
    const updatedAtTime = updatedAt.getTime();
    const todayTime = startOfToday.getTime();
    const label =
      updatedAtTime >= todayTime
        ? "Today"
        : updatedAtTime >= todayTime - dayInMilliseconds
          ? "Yesterday"
          : updatedAtTime >= todayTime - dayInMilliseconds * 7
            ? "Previous 7 days"
            : "Older";
    groups.get(label)?.push(conversation);
  }
  return Array.from(groups, ([label, items]) => ({ label, items })).filter(
    (group) => group.items.length > 0,
  );
}

export function apiMessagesToItems(
  messages: ChatMessageView[],
): ChatMessageItem[] {
  return messages.map((message) => ({
    id: message.id,
    direction: message.direction,
    content: message.content,
    createdAt: message.created_at,
    delivery: "saved",
    source: message.direction === "outbound" ? "Saved conversation" : undefined,
  }));
}
