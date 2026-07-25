import React, { createRef } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatMessageList } from "@/components/chat/chat-message-list";
import type { ChatState } from "@/lib/chat-state";

afterEach(cleanup);

describe("chat generated media", () => {
  it("renders durable images, videos and downloadable documents", () => {
    const state: ChatState = {
      conversationId: "conversation-1",
      draft: "",
      phase: "ready",
      loadError: null,
      failedSend: null,
      olderCursor: null,
      activeLoadRequestId: null,
      activeSendRequestId: null,
      messages: [
        {
          id: "outbound-1",
          direction: "outbound",
          content: "Your files are ready.",
          createdAt: "2026-07-25T10:00:00Z",
          delivery: "saved",
          generatedMedia: [
            {
              id: "image-1",
              media_type: "image",
              mime_type: "image/png",
              filename: "poster.png",
              byte_size: 1_024,
              url: "/v1/chat/media/image-1",
            },
            {
              id: "video-1",
              media_type: "video",
              mime_type: "video/mp4",
              filename: "campaign.mp4",
              byte_size: 2_048,
              url: "/v1/chat/media/video-1",
            },
            {
              id: "document-1",
              media_type: "document",
              mime_type: "text/csv",
              filename: "restock.csv",
              byte_size: 3_072,
              url: "/v1/chat/media/document-1",
            },
          ],
        },
      ],
    };
    const noop = vi.fn();
    const { container } = render(
      <ChatMessageList
        endRef={createRef<HTMLDivElement>()}
        loadingOlder={false}
        onLoadOlder={noop}
        onRetryConversation={noop}
        onRetrySend={noop}
        onSuggestion={noop}
        state={state}
      />,
    );

    expect(screen.getByAltText("poster.png")).toHaveAttribute(
      "src",
      "/v1/chat/media/image-1",
    );
    expect(container.querySelector("video source")).toHaveAttribute(
      "src",
      "/v1/chat/media/video-1",
    );
    expect(screen.getByRole("link", { name: /restock.csv/i })).toHaveAttribute(
      "href",
      "/v1/chat/media/document-1",
    );
  });
});
