import { describe, expect, it } from "vitest";

import { historyReducer, type HistoryState } from "@/lib/use-chat-history";

const initialState: HistoryState = {
  items: [],
  nextCursor: null,
  phase: "ready",
  error: null,
  activeRequestId: null,
};

describe("chat history state", () => {
  it("appends cursor pages without duplicating conversations", () => {
    const existing = {
      id: "conversation-1",
      title: "Current stock",
      channel: "web" as const,
      updated_at: "2026-07-21T08:00:00Z",
      last_message_preview: "Current stock",
    };
    const loading = historyReducer(
      { ...initialState, items: [existing], nextCursor: "cursor-2" },
      { type: "LOADING", requestId: "page-2" },
    );
    const loaded = historyReducer(loading, {
      type: "LOADED",
      requestId: "page-2",
      append: true,
      page: {
        items: [
          existing,
          {
            id: "conversation-2",
            title: "Older stock",
            channel: "web",
            updated_at: "2026-07-20T08:00:00Z",
            last_message_preview: "Older stock",
          },
        ],
        next_cursor: null,
      },
    });

    expect(loaded.items.map((item) => item.id)).toEqual([
      "conversation-1",
      "conversation-2",
    ]);
    expect(loaded.nextCursor).toBeNull();
    expect(loaded.phase).toBe("ready");
  });

  it("ignores stale cursor results after a refresh starts", () => {
    const loadingOlder = historyReducer(
      { ...initialState, nextCursor: "cursor-2" },
      { type: "LOADING", requestId: "older" },
    );
    const refreshing = historyReducer(loadingOlder, {
      type: "LOADING",
      requestId: "refresh",
    });
    const staleResult = historyReducer(refreshing, {
      type: "LOADED",
      requestId: "older",
      append: true,
      page: {
        items: [
          {
            id: "stale-conversation",
            title: "Stale conversation",
            channel: "web",
            updated_at: "2026-07-01T08:00:00Z",
            last_message_preview: null,
          },
        ],
        next_cursor: null,
      },
    });

    expect(staleResult).toBe(refreshing);
    expect(staleResult.items).toEqual([]);
  });

  it("keeps a recoverable cursor after a page request fails", () => {
    const loading = historyReducer(
      { ...initialState, nextCursor: "cursor-retry" },
      { type: "LOADING", requestId: "page-retry" },
    );
    const failed = historyReducer(loading, {
      type: "FAILED",
      requestId: "page-retry",
      error: "Network unavailable",
    });

    expect(failed.nextCursor).toBe("cursor-retry");
    expect(failed.phase).toBe("error");
    expect(failed.error).toBe("Network unavailable");
  });
});
