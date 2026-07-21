"use client";

import { ChatCircleIcon, ListIcon, PlusIcon } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ChatComposer } from "@/components/chat/chat-composer";
import { ChatMessageList } from "@/components/chat/chat-message-list";
import { ChatSidebar } from "@/components/chat/chat-sidebar";
import { apiRequest } from "@/lib/api";
import {
  apiMessagesToItems,
  chatReducer,
  initialChatState,
  type ChatMessagePage,
  type ChatResponse,
  type ConversationSummary,
} from "@/lib/chat-state";
import { useChatHistory } from "@/lib/use-chat-history";

async function logout() {
  try {
    await apiRequest<unknown>("/auth/logout", { method: "POST" });
  } finally {
    window.location.assign("/login");
  }
}

export function ChatWorkspace({
  initialConversationId = null,
}: {
  initialConversationId?: string | null;
}) {
  const router = useRouter();
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const activeLoadRequestRef = useRef<string | null>(null);
  const activeSendRequestRef = useRef<string | null>(null);
  const locallyCreatedConversationRef = useRef<string | null>(null);
  const {
    conversations,
    error: historyError,
    hasMore,
    loading: historyLoading,
    loadMore,
    refresh: refreshHistory,
  } = useChatHistory();

  useEffect(() => {
    const previousDocumentOverflow = document.documentElement.style.overflow;
    const previousBodyOverflow = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    window.scrollTo({ top: 0, behavior: "instant" });
    return () => {
      document.documentElement.style.overflow = previousDocumentOverflow;
      document.body.style.overflow = previousBodyOverflow;
    };
  }, []);

  useEffect(() => {
    const keepWorkspacePinned = () =>
      window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    keepWorkspacePinned();
    const frame = window.requestAnimationFrame(keepWorkspacePinned);
    return () => window.cancelAnimationFrame(frame);
  }, [sidebarCollapsed]);

  const loadConversation = useCallback(async (conversationId: string) => {
    const requestId = crypto.randomUUID();
    activeLoadRequestRef.current = requestId;
    activeSendRequestRef.current = null;
    dispatch({ type: "LOAD_STARTED", conversationId, requestId });
    try {
      const page = await apiRequest<ChatMessagePage>(
        `/chat/conversations/${encodeURIComponent(conversationId)}/messages?limit=50`,
      );
      dispatch({
        type: "LOAD_SUCCEEDED",
        conversationId,
        requestId,
        messages: apiMessagesToItems(page.items),
        olderCursor: page.next_cursor ?? null,
      });
      if (activeLoadRequestRef.current === requestId) {
        activeLoadRequestRef.current = null;
      }
    } catch (reason) {
      if (activeLoadRequestRef.current !== requestId) return;
      dispatch({
        type: "LOAD_FAILED",
        conversationId,
        requestId,
        error:
          reason instanceof Error
            ? reason.message
            : "This conversation could not be loaded.",
      });
      activeLoadRequestRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (initialConversationId) {
      if (locallyCreatedConversationRef.current === initialConversationId) {
        locallyCreatedConversationRef.current = null;
        return;
      }
      void loadConversation(initialConversationId);
    } else {
      dispatch({ type: "RESET" });
    }
  }, [initialConversationId, loadConversation]);

  useEffect(() => {
    const end = endRef.current;
    const scrollContainer = end?.closest<HTMLElement>(".bestie-message-scroll");
    if (!scrollContainer) return;
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    scrollContainer.scrollTo({
      top: scrollContainer.scrollHeight,
      behavior:
        ["sending", "retrying"].includes(state.phase) && !reduceMotion
          ? "smooth"
          : "instant",
    });
  }, [state.messages, state.phase]);

  const openConversation = (conversation: ConversationSummary) => {
    activeLoadRequestRef.current = null;
    activeSendRequestRef.current = null;
    setMobileSidebarOpen(false);
    router.push(`/chat/${encodeURIComponent(conversation.id)}`, {
      scroll: false,
    });
  };

  const startNewChat = () => {
    activeLoadRequestRef.current = null;
    activeSendRequestRef.current = null;
    setMobileSidebarOpen(false);
    dispatch({ type: "RESET" });
    router.push("/chat", { scroll: false });
  };

  const completeSend = async (
    text: string,
    clientMessageId: string,
    requestId: string,
  ) => {
    try {
      const response = await apiRequest<ChatResponse>("/chat/web", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          conversation_id: state.conversationId,
          client_message_id: clientMessageId,
        }),
      });
      const source = response.data_freshness
        ? `Business data refreshed ${new Date(response.data_freshness).toLocaleString()}`
        : "Bumpa Bestie workspace";
      if (activeSendRequestRef.current !== requestId) return;
      locallyCreatedConversationRef.current = response.conversation_id;
      dispatch({ type: "SEND_SUCCEEDED", requestId, response, source });
      router.replace(`/chat/${response.conversation_id}`, { scroll: false });
      void refreshHistory();
      activeSendRequestRef.current = null;
    } catch (reason) {
      if (activeSendRequestRef.current !== requestId) return;
      dispatch({
        type: "SEND_FAILED",
        requestId,
        error:
          reason instanceof Error
            ? reason.message
            : "The assistant is unavailable right now.",
      });
      activeSendRequestRef.current = null;
    }
  };

  const sendMessage = (suggestedText?: string) => {
    const text = (suggestedText ?? state.draft).trim();
    if (!text || ["sending", "retrying"].includes(state.phase)) return;
    const clientMessageId = crypto.randomUUID();
    const requestId = crypto.randomUUID();
    activeSendRequestRef.current = requestId;
    dispatch({
      type: "SEND_STARTED",
      userMessageId: `pending-${clientMessageId}`,
      text,
      clientMessageId,
      requestId,
    });
    void completeSend(text, clientMessageId, requestId);
  };

  const retrySend = () => {
    if (!state.failedSend || ["sending", "retrying"].includes(state.phase))
      return;
    const requestId = crypto.randomUUID();
    activeSendRequestRef.current = requestId;
    dispatch({ type: "RETRY_STARTED", requestId });
    void completeSend(
      state.failedSend.text,
      state.failedSend.clientMessageId,
      requestId,
    );
  };

  const loadOlder = async () => {
    if (!state.conversationId || !state.olderCursor || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const page = await apiRequest<ChatMessagePage>(
        `/chat/conversations/${encodeURIComponent(state.conversationId)}/messages?limit=50&cursor=${encodeURIComponent(state.olderCursor)}`,
      );
      dispatch({
        type: "OLDER_MESSAGES_LOADED",
        conversationId: state.conversationId,
        messages: apiMessagesToItems(page.items),
        olderCursor: page.next_cursor ?? null,
      });
    } finally {
      setLoadingOlder(false);
    }
  };

  return (
    <main
      className={`bestie-workspace ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}
    >
      <ChatSidebar
        activeConversationId={state.conversationId ?? initialConversationId}
        collapsed={sidebarCollapsed}
        conversations={conversations}
        error={historyError}
        hasMore={hasMore}
        loading={historyLoading}
        mobileOpen={mobileSidebarOpen}
        onCloseMobile={() => setMobileSidebarOpen(false)}
        onLoadMore={() => void loadMore()}
        onLogout={() => void logout()}
        onNewChat={startNewChat}
        onRetry={() => void refreshHistory()}
        onSelect={openConversation}
        onToggleCollapsed={() => setSidebarCollapsed((current) => !current)}
      />
      <section className="bestie-chat" aria-label="Bumpa Bestie conversation">
        <header className="bestie-mobile-header">
          <button
            type="button"
            aria-label="Open conversation history"
            onClick={() => setMobileSidebarOpen(true)}
          >
            <ListIcon size={21} />
          </button>
          <span>
            <ChatCircleIcon size={19} weight="fill" /> Bumpa Bestie
          </span>
          <button
            type="button"
            aria-label="Start a new chat"
            onClick={startNewChat}
          >
            <PlusIcon size={21} />
          </button>
        </header>
        <ChatMessageList
          endRef={endRef}
          loadingOlder={loadingOlder}
          onLoadOlder={() => void loadOlder()}
          onRetryConversation={() => {
            if (state.conversationId)
              void loadConversation(state.conversationId);
          }}
          onRetrySend={retrySend}
          onSuggestion={sendMessage}
          state={state}
        />
        <ChatComposer
          disabled={["sending", "retrying"].includes(state.phase)}
          draft={state.draft}
          onDraftChange={(draft) => dispatch({ type: "DRAFT_CHANGED", draft })}
          onSend={() => sendMessage()}
        />
      </section>
    </main>
  );
}
