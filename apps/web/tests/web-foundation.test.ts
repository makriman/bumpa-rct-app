import { describe, expect, it, vi } from "vitest";

import {
  hasActiveConsumerMembership,
  parseApiErrorPayload,
  platformRolesFromSession,
  readUrlFilters,
  responseError,
  trapTabKey,
  writeUrlFilters,
} from "@bumpabestie/web-foundation";

describe("shared web foundation", () => {
  it("normalizes structured API errors without exposing response bodies", () => {
    const error = responseError(
      new Response(null, {
        status: 503,
        headers: { "X-Correlation-ID": "correlation-1" },
      }),
      {
        detail: {
          code: "provider_unavailable",
          message: "The service is temporarily unavailable.",
          retryable: true,
        },
      },
    );

    expect(error).toMatchObject({
      status: 503,
      code: "provider_unavailable",
      message: "The service is temporarily unavailable.",
      retryable: true,
      correlationId: "correlation-1",
    });
  });

  it("validates untrusted error and session payloads at the boundary", () => {
    expect(
      parseApiErrorPayload({
        detail: { code: "busy", message: "Try again", retryable: true },
      }),
    ).toEqual({
      detail: { code: "busy", message: "Try again", retryable: true },
      error: undefined,
    });
    expect(parseApiErrorPayload(["not", "an", "object"])).toBeNull();
    expect(
      platformRolesFromSession({
        platform_roles: ["operator", 42, null, "superadmin"],
      }),
    ).toEqual(["operator", "superadmin"]);
    expect(
      hasActiveConsumerMembership({
        memberships: [{ role: "owner", status: "active" }],
      }),
    ).toBe(true);
    expect(
      hasActiveConsumerMembership({
        memberships: [{ role: "researcher", status: "active" }],
      }),
    ).toBe(false);
  });

  it("wraps keyboard focus inside an accessible boundary", () => {
    const boundary = document.createElement("div");
    const first = document.createElement("button");
    const last = document.createElement("button");
    boundary.append(first, last);
    document.body.append(boundary);
    const preventDefault = vi.fn();

    last.focus();
    expect(
      trapTabKey({ key: "Tab", shiftKey: false, preventDefault }, boundary),
    ).toBe(true);
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(first).toHaveFocus();

    first.focus();
    expect(
      trapTabKey({ key: "Tab", shiftKey: true, preventDefault }, boundary),
    ).toBe(true);
    expect(last).toHaveFocus();
    boundary.remove();
  });

  it("round-trips valid URL filters while preserving unrelated parameters", () => {
    const definitions = {
      q: { defaultValue: "" },
      status: {
        defaultValue: "all",
        allowedValues: ["all", "active", "suspended"],
      },
    } as const;

    expect(readUrlFilters("?q=Kaia&status=active", definitions)).toEqual({
      q: "Kaia",
      status: "active",
    });
    expect(
      writeUrlFilters(
        "https://admin.bumpabestie.com/tenants?tab=recent#directory",
        definitions,
        { q: "Kaia Home", status: "active" },
      ),
    ).toBe("/tenants?tab=recent&q=Kaia+Home&status=active#directory");
  });

  it("drops defaults and rejects malformed URL filter values", () => {
    const definitions = {
      q: { defaultValue: "" },
      status: {
        defaultValue: "all",
        allowedValues: ["all", "active", "suspended"],
      },
    } as const;

    expect(readUrlFilters("?status=deleted", definitions)).toEqual({
      q: "",
      status: "all",
    });
    expect(
      writeUrlFilters(
        "https://admin.bumpabestie.com/tenants?q=old&status=active",
        definitions,
        { q: "", status: "all" },
      ),
    ).toBe("/tenants");
  });
});
