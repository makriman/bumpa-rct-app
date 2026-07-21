"use client";

import {
  CaretLeftIcon,
  CaretRightIcon,
  ChatCircleIcon,
  GearIcon,
  ListIcon,
  PlusIcon,
  SignOutIcon,
  UserCircleIcon,
  XIcon,
} from "@phosphor-icons/react";
import Link from "next/link";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { trapTabKey } from "@bumpabestie/web-foundation";
import type { ConversationSummary } from "@/lib/chat-state";
import { groupConversations } from "@/lib/chat-state";

type ChatSidebarProps = {
  activeConversationId: string | null;
  collapsed: boolean;
  conversations: ConversationSummary[];
  error: string | null;
  hasMore: boolean;
  loading: boolean;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  onLoadMore: () => void;
  onLogout: () => void;
  onNewChat: () => void;
  onRetry: () => void;
  onSelect: (conversation: ConversationSummary) => void;
  onToggleCollapsed: () => void;
};

export function ChatSidebar({
  activeConversationId,
  collapsed,
  conversations,
  error,
  hasMore,
  loading,
  mobileOpen,
  onCloseMobile,
  onLoadMore,
  onLogout,
  onNewChat,
  onRetry,
  onSelect,
  onToggleCollapsed,
}: ChatSidebarProps) {
  const groups = groupConversations(conversations);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const [isMobileViewport, setIsMobileViewport] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 820px)");
    const updateViewport = () => setIsMobileViewport(media.matches);
    updateViewport();
    media.addEventListener("change", updateViewport);
    return () => media.removeEventListener("change", updateViewport);
  }, []);

  useEffect(() => {
    if (!mobileOpen || !isMobileViewport) return;

    const previouslyFocused = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseMobile();
        return;
      }
      trapTabKey(event, sidebarRef.current);
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  }, [isMobileViewport, mobileOpen, onCloseMobile]);

  return (
    <>
      {mobileOpen && (
        <button
          type="button"
          className="bestie-sidebar-scrim"
          aria-hidden="true"
          tabIndex={-1}
          onClick={onCloseMobile}
        />
      )}
      <aside
        ref={sidebarRef}
        className={`bestie-sidebar ${collapsed ? "is-collapsed" : ""} ${mobileOpen ? "is-mobile-open" : ""}`}
        aria-label="Conversation history"
        aria-hidden={isMobileViewport && !mobileOpen ? true : undefined}
        aria-modal={isMobileViewport && mobileOpen ? true : undefined}
        inert={isMobileViewport && !mobileOpen ? true : undefined}
        role={isMobileViewport && mobileOpen ? "dialog" : undefined}
      >
        <div className="bestie-sidebar-header">
          <Link
            className="bestie-brand"
            href="/chat"
            aria-label="Bumpa Bestie chat home"
          >
            <Image
              className="bestie-brand-mark"
              src="/brand-mark.svg"
              alt=""
              width={34}
              height={34}
            />
            <span className="bestie-brand-name">Bumpa Bestie</span>
          </Link>
          <button
            ref={closeButtonRef}
            type="button"
            className="bestie-icon-button bestie-mobile-close"
            aria-label="Close conversation history"
            onClick={onCloseMobile}
          >
            <XIcon size={20} />
          </button>
        </div>

        <button
          type="button"
          className="bestie-new-chat"
          aria-label="New chat"
          onClick={onNewChat}
        >
          <PlusIcon size={18} weight="bold" />
          <span>New chat</span>
        </button>

        <nav className="bestie-history" aria-label="Recent chats">
          {!collapsed && loading && conversations.length === 0 && (
            <div className="bestie-history-loading" aria-busy="true">
              <span className="sr-only">Loading recent chats</span>
              <i />
              <i />
              <i />
            </div>
          )}
          {!collapsed && error && (
            <div className="bestie-sidebar-error" role="alert">
              <strong>Recent chats are unavailable</strong>
              <span>{error}</span>
              <button type="button" onClick={onRetry}>
                Try again
              </button>
            </div>
          )}
          {!collapsed && !loading && !error && conversations.length === 0 && (
            <p className="bestie-sidebar-empty">
              Your recent chats will appear here.
            </p>
          )}
          {!collapsed &&
            groups.map((group) => (
              <section className="bestie-history-group" key={group.label}>
                <h2>{group.label}</h2>
                {group.items.map((conversation) => (
                  <button
                    type="button"
                    className={`bestie-history-item ${activeConversationId === conversation.id ? "is-active" : ""}`}
                    key={conversation.id}
                    aria-current={
                      activeConversationId === conversation.id
                        ? "page"
                        : undefined
                    }
                    onClick={() => onSelect(conversation)}
                  >
                    <ChatCircleIcon size={17} />
                    <span>
                      <strong>{conversation.title || "Untitled chat"}</strong>
                      {conversation.last_message_preview && (
                        <small>{conversation.last_message_preview}</small>
                      )}
                    </span>
                  </button>
                ))}
              </section>
            ))}
          {!collapsed && hasMore && (
            <button
              type="button"
              className="bestie-load-more"
              disabled={loading}
              onClick={onLoadMore}
            >
              {loading ? "Loading…" : "Show more"}
            </button>
          )}
        </nav>

        <div className="bestie-sidebar-footer">
          <details className="bestie-account-menu">
            <summary aria-label="Open account menu">
              <span className="bestie-account-avatar" aria-hidden="true">
                <UserCircleIcon size={22} />
              </span>
              <span className="bestie-account-copy">
                <strong>Your account</strong>
                <small>Workspace settings</small>
              </span>
              <GearIcon className="bestie-account-gear" size={18} />
            </summary>
            <div className="bestie-account-popover">
              <Link href="/profile">
                <UserCircleIcon size={18} /> Profile
              </Link>
              <Link href="/settings/team">
                <ListIcon size={18} /> Team
              </Link>
              <Link href="/settings/whatsapp">
                <ChatCircleIcon size={18} /> WhatsApp
              </Link>
              <Link href="/settings/bumpa">
                <GearIcon size={18} /> Bumpa connection
              </Link>
              <Link href="/settings/mcp">
                <ListIcon size={18} /> Connections
              </Link>
              <button type="button" onClick={onLogout}>
                <SignOutIcon size={18} /> Log out
              </button>
            </div>
          </details>
          <button
            type="button"
            className="bestie-collapse-button"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={onToggleCollapsed}
          >
            {collapsed ? (
              <CaretRightIcon size={18} />
            ) : (
              <CaretLeftIcon size={18} />
            )}
          </button>
        </div>
      </aside>
    </>
  );
}
