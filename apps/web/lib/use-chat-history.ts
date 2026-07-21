"use client";

import { useCallback, useEffect, useReducer } from "react";
import { apiRequest } from "@/lib/api";
import type {
  ConversationSummary,
  ConversationSummaryPage,
} from "@/lib/chat-state";

export type HistoryState = {
  items: ConversationSummary[];
  nextCursor: string | null;
  phase: "loading" | "ready" | "error";
  error: string | null;
  activeRequestId: string | null;
};

export type HistoryAction =
  | { type: "LOADING"; requestId: string }
  | {
      type: "LOADED";
      requestId: string;
      page: ConversationSummaryPage;
      append: boolean;
    }
  | { type: "FAILED"; requestId: string; error: string };

const initialState: HistoryState = {
  items: [],
  nextCursor: null,
  phase: "loading",
  error: null,
  activeRequestId: null,
};

export function historyReducer(
  state: HistoryState,
  action: HistoryAction,
): HistoryState {
  switch (action.type) {
    case "LOADING":
      return {
        ...state,
        phase: "loading",
        error: null,
        activeRequestId: action.requestId,
      };
    case "LOADED":
      if (state.activeRequestId !== action.requestId) return state;
      return {
        items: action.append
          ? [
              ...state.items,
              ...action.page.items.filter(
                (item) =>
                  !state.items.some((current) => current.id === item.id),
              ),
            ]
          : action.page.items,
        nextCursor: action.page.next_cursor ?? null,
        phase: "ready",
        error: null,
        activeRequestId: null,
      };
    case "FAILED":
      if (state.activeRequestId !== action.requestId) return state;
      return {
        ...state,
        phase: "error",
        error: action.error,
        activeRequestId: null,
      };
  }
}

export function useChatHistory() {
  const [state, dispatch] = useReducer(historyReducer, initialState);

  const refresh = useCallback(async () => {
    const requestId = crypto.randomUUID();
    dispatch({ type: "LOADING", requestId });
    try {
      const page = await apiRequest<ConversationSummaryPage>(
        "/chat/conversations/page?limit=30",
      );
      dispatch({ type: "LOADED", requestId, page, append: false });
    } catch (reason) {
      dispatch({
        type: "FAILED",
        requestId,
        error:
          reason instanceof Error
            ? reason.message
            : "Conversation history is unavailable.",
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const loadMore = useCallback(async () => {
    if (!state.nextCursor || state.phase === "loading") return;
    const requestId = crypto.randomUUID();
    dispatch({ type: "LOADING", requestId });
    try {
      const page = await apiRequest<ConversationSummaryPage>(
        `/chat/conversations/page?limit=30&cursor=${encodeURIComponent(state.nextCursor)}`,
      );
      dispatch({ type: "LOADED", requestId, page, append: true });
    } catch (reason) {
      dispatch({
        type: "FAILED",
        requestId,
        error:
          reason instanceof Error
            ? reason.message
            : "More conversations could not be loaded.",
      });
    }
  }, [state.nextCursor, state.phase]);

  return {
    conversations: state.items,
    error: state.error,
    hasMore: Boolean(state.nextCursor),
    loading: state.phase === "loading",
    loadMore,
    refresh,
  };
}
