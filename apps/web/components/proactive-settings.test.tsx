import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProactiveSettings } from "@/components/proactive-settings";

type Cadence = "daily" | "weekly";
type Subscription = {
  id: string | null;
  cadence: Cadence;
  status: "off" | "active" | "paused";
  revision: number;
  phone_identity_id: string | null;
  recipient_preview: string | null;
  timezone: string;
  local_hour: number;
  weekday: number;
  effective: boolean;
  delivery_available: boolean;
};

function subscription(
  cadence: Cadence,
  overrides: Partial<Subscription> = {},
): Subscription {
  return {
    id: null,
    cadence,
    status: "off",
    revision: 0,
    phone_identity_id: null,
    recipient_preview: null,
    timezone: "Africa/Lagos",
    local_hour: 8,
    weekday: 0,
    effective: false,
    delivery_available: true,
    ...overrides,
  };
}

function settings(
  subscriptions = [subscription("daily"), subscription("weekly")],
) {
  return {
    subscriptions,
    recipients: [
      { id: "phone-primary", phone_e164: "+15550102010", label: "My primary" },
      { id: "phone-backup", phone_e164: "+15550102011", label: "My backup" },
    ],
    store_timezone: "Africa/Lagos",
    notice: "Optional business messages. Research participation is separate.",
  };
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function mockFetch(
  initial = settings(),
  save: () => Promise<Response> = async () => {
    throw new Error("Unexpected subscription write");
  },
) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      if (String(input) !== "/api/backend/settings/proactive-insights") {
        throw new Error("Unexpected settings endpoint");
      }
      return init?.method === "PUT" ? save() : jsonResponse(initial);
    });
}

function puts(fetchMock: ReturnType<typeof mockFetch>) {
  return fetchMock.mock.calls.filter(
    ([, request]) => request?.method === "PUT",
  );
}

async function form(cadence: Cadence) {
  return within(
    await screen.findByRole("region", { name: `${cadence} business summary` }),
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("optional business summary settings", () => {
  it("starts both cadences off and never enrolls a number on load or schedule edits", async () => {
    const fetchMock = mockFetch();
    render(<ProactiveSettings />);
    const daily = await form("daily");
    const weekly = await form("weekly");

    expect(daily.getByText("Off")).toBeVisible();
    expect(weekly.getByText("Off")).toBeVisible();
    expect(daily.getByLabelText("Send only to my selected number")).toHaveValue(
      "",
    );
    expect(
      weekly.getByLabelText("Send only to my selected number"),
    ).toHaveValue("");
    expect(
      daily.getByRole("button", { name: "Subscribe daily" }),
    ).toBeDisabled();
    expect(
      weekly.getByRole("button", { name: "Subscribe weekly" }),
    ).toBeDisabled();
    expect(
      screen.getByText(/Research participation is separate/),
    ).toBeVisible();
    expect(
      screen.getByText(/START alone does not create a subscription/),
    ).toBeVisible();
    expect(daily.queryByLabelText("Delivery day")).not.toBeInTheDocument();

    fireEvent.change(daily.getByLabelText("Send only to my selected number"), {
      target: { value: "phone-backup" },
    });
    fireEvent.change(daily.getByLabelText("Delivery timezone"), {
      target: { value: "   " },
    });
    expect(
      daily.getByRole("button", { name: "Subscribe daily" }),
    ).toBeDisabled();
    fireEvent.change(daily.getByLabelText("Delivery timezone"), {
      target: { value: "Europe/London" },
    });
    fireEvent.change(daily.getByLabelText("Send at or after local hour"), {
      target: { value: "14" },
    });
    expect(
      daily.getByRole("button", { name: "Subscribe daily" }),
    ).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(puts(fetchMock)).toHaveLength(0);
  });

  it("requires an eligible approved recipient rather than inventing or enrolling one", async () => {
    const initial = { ...settings(), recipients: [] };
    const fetchMock = mockFetch(initial);
    render(<ProactiveSettings />);
    const daily = await form("daily");

    expect(
      daily.getByText(/No eligible number belongs to your account/),
    ).toBeVisible();
    expect(
      daily.getByText(/New numbers are never enrolled automatically/),
    ).toBeVisible();
    expect(
      daily.getByRole("button", { name: "Subscribe daily" }),
    ).toBeDisabled();
    expect(puts(fetchMock)).toHaveLength(0);
  });

  it.each(["daily", "weekly"] as const)(
    "subscribes %s only after an explicit click with the previewed recipient, time and exact revision",
    async (cadence) => {
      const saved = subscription(cadence, {
        id: "saved-subscription",
        status: "active",
        revision: 1,
        phone_identity_id: "phone-backup",
        recipient_preview: "+15550102011",
        timezone: "America/New_York",
        local_hour: 17,
        weekday: cadence === "weekly" ? 4 : 0,
        effective: true,
      });
      const fetchMock = mockFetch(settings(), async () =>
        jsonResponse(
          settings([
            saved,
            subscription(cadence === "daily" ? "weekly" : "daily"),
          ]),
        ),
      );
      render(<ProactiveSettings />);
      const controls = await form(cadence);

      fireEvent.change(
        controls.getByLabelText("Send only to my selected number"),
        {
          target: { value: "phone-backup" },
        },
      );
      fireEvent.change(controls.getByLabelText("Delivery timezone"), {
        target: { value: "America/New_York" },
      });
      fireEvent.change(controls.getByLabelText("Send at or after local hour"), {
        target: { value: "17" },
      });
      if (cadence === "weekly") {
        fireEvent.change(controls.getByLabelText("Delivery day"), {
          target: { value: "4" },
        });
      }
      expect(
        controls.getByText(
          `Recipient preview: +15550102011 · ${cadence === "weekly" ? "Friday" : "Daily"} 17:00 America/New_York.`,
        ),
      ).toBeVisible();
      expect(puts(fetchMock)).toHaveLength(0);
      fireEvent.click(
        controls.getByRole("button", { name: `Subscribe ${cadence}` }),
      );

      expect(
        await screen.findByText("Active", { selector: ".badge" }),
      ).toBeVisible();
      expect(puts(fetchMock)).toHaveLength(1);
      const [path, request] = puts(fetchMock)[0];
      expect(path).toBe("/api/backend/settings/proactive-insights");
      expect(request).toMatchObject({
        method: "PUT",
        credentials: "same-origin",
      });
      expect(JSON.parse(String(request?.body))).toEqual({
        cadence,
        status: "active",
        expected_revision: 0,
        phone_identity_id: "phone-backup",
        timezone: "America/New_York",
        local_hour: 17,
        weekday: cadence === "weekly" ? 4 : 0,
      });
      expect(
        (await form(cadence)).getByRole("button", { name: "Save schedule" }),
      ).toBeEnabled();
      expect(
        (await form(cadence === "daily" ? "weekly" : "daily")).getByText("Off"),
      ).toBeVisible();
    },
  );

  it("updates an active schedule using its loaded revision and the newly selected exact number", async () => {
    const active = subscription("weekly", {
      id: "weekly-existing",
      status: "active",
      revision: 7,
      phone_identity_id: "phone-primary",
      effective: true,
    });
    const updated = {
      ...active,
      revision: 8,
      phone_identity_id: "phone-backup",
      timezone: "Europe/London",
      local_hour: 21,
      weekday: 6,
    };
    const save = vi
      .fn<() => Promise<Response>>()
      .mockResolvedValueOnce(jsonResponse(settings([updated])))
      .mockResolvedValueOnce(
        jsonResponse(
          settings([
            {
              ...updated,
              status: "paused",
              revision: 9,
              effective: false,
            },
          ]),
        ),
      );
    const fetchMock = mockFetch(settings([active]), save);
    render(<ProactiveSettings />);
    const weekly = await form("weekly");
    fireEvent.change(weekly.getByLabelText("Send only to my selected number"), {
      target: { value: "phone-backup" },
    });
    fireEvent.change(weekly.getByLabelText("Delivery timezone"), {
      target: { value: "Europe/London" },
    });
    fireEvent.change(weekly.getByLabelText("Send at or after local hour"), {
      target: { value: "21" },
    });
    fireEvent.change(weekly.getByLabelText("Delivery day"), {
      target: { value: "6" },
    });
    fireEvent.click(weekly.getByRole("button", { name: "Save schedule" }));

    expect(
      await screen.findByRole("button", { name: "Save schedule" }),
    ).toBeEnabled();
    expect(JSON.parse(String(puts(fetchMock)[0][1]?.body))).toEqual({
      cadence: "weekly",
      status: "active",
      expected_revision: 7,
      phone_identity_id: "phone-backup",
      timezone: "Europe/London",
      local_hour: 21,
      weekday: 6,
    });
    const fresh = await form("weekly");
    fireEvent.click(fresh.getByRole("button", { name: "Pause weekly" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(2));
    expect(JSON.parse(String(puts(fetchMock)[1][1]?.body))).toMatchObject({
      status: "paused",
      expected_revision: 8,
    });
    expect(
      await screen.findByText("Paused", { selector: ".badge" }),
    ).toBeVisible();
  });

  it("uses the saved server revision and schedule after normalizing an edited draft", async () => {
    const saved = subscription("weekly", {
      id: "weekly-saved",
      status: "active",
      revision: 1,
      phone_identity_id: "phone-backup",
      timezone: "Europe/London",
      local_hour: 9,
      weekday: 2,
      effective: true,
    });
    const fetchMock = mockFetch(settings(), async () =>
      jsonResponse(settings([saved])),
    );
    render(<ProactiveSettings />);
    const weekly = await form("weekly");
    fireEvent.change(weekly.getByLabelText("Send only to my selected number"), {
      target: { value: "phone-primary" },
    });
    fireEvent.change(weekly.getByLabelText("Delivery timezone"), {
      target: { value: "America/New_York" },
    });
    fireEvent.click(weekly.getByRole("button", { name: "Subscribe weekly" }));

    await screen.findByRole("button", { name: "Save schedule" });
    const fresh = await form("weekly");
    expect(fresh.getByLabelText("Delivery timezone")).toHaveValue(
      "Europe/London",
    );
    expect(fresh.getByLabelText("Send only to my selected number")).toHaveValue(
      "phone-backup",
    );
    expect(fresh.getByLabelText("Send at or after local hour")).toHaveValue(
      "9",
    );
    expect(fresh.getByLabelText("Delivery day")).toHaveValue("2");
    fireEvent.click(fresh.getByRole("button", { name: "Pause weekly" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(2));
    expect(JSON.parse(String(puts(fetchMock)[1][1]?.body))).toMatchObject({
      expected_revision: 1,
      status: "paused",
      phone_identity_id: "phone-backup",
      timezone: "Europe/London",
      local_hour: 9,
      weekday: 2,
    });
  });

  it("pauses an active subscription with its revision and applies the server's paused state", async () => {
    const active = subscription("daily", {
      id: "daily-existing",
      status: "active",
      revision: 4,
      phone_identity_id: "phone-primary",
      effective: true,
    });
    const fetchMock = mockFetch(settings([active]), async () =>
      jsonResponse(
        settings([
          { ...active, status: "paused", revision: 5, effective: false },
        ]),
      ),
    );
    render(<ProactiveSettings />);
    const daily = await form("daily");
    fireEvent.click(daily.getByRole("button", { name: "Pause daily" }));

    expect(
      await screen.findByText("Paused", { selector: ".badge" }),
    ).toBeVisible();
    expect(JSON.parse(String(puts(fetchMock)[0][1]?.body))).toEqual({
      cadence: "daily",
      status: "paused",
      expected_revision: 4,
      phone_identity_id: "phone-primary",
      timezone: "Africa/Lagos",
      local_hour: 8,
      weekday: 0,
    });
    expect(
      (await form("daily")).queryByRole("button", { name: "Pause daily" }),
    ).not.toBeInTheDocument();
    expect(
      (await form("daily")).getByRole("button", { name: "Subscribe daily" }),
    ).toBeEnabled();
    expect(puts(fetchMock)).toHaveLength(1);
  });

  it("still lets the user pause when a previously selected number is no longer eligible", async () => {
    const active = subscription("daily", {
      id: "daily-existing",
      status: "active",
      revision: 3,
      phone_identity_id: "removed-phone",
      effective: false,
    });
    const initial = { ...settings([active]), recipients: [] };
    const fetchMock = mockFetch(initial, async () =>
      jsonResponse({
        ...initial,
        subscriptions: [{ ...active, status: "paused", revision: 4 }],
      }),
    );
    render(<ProactiveSettings />);
    const daily = await form("daily");
    expect(daily.getByRole("button", { name: "Save schedule" })).toBeDisabled();
    expect(daily.getByRole("button", { name: "Pause daily" })).toBeEnabled();
    fireEvent.click(daily.getByRole("button", { name: "Pause daily" }));

    expect(
      await screen.findByText("Paused", { selector: ".badge" }),
    ).toBeVisible();
    expect(JSON.parse(String(puts(fetchMock)[0][1]?.body))).toMatchObject({
      cadence: "daily",
      status: "paused",
      expected_revision: 3,
    });
  });

  it.each([
    [409, "These settings changed. Reload and review before saving"],
    [403, "This recipient is no longer approved for your account"],
  ])(
    "surfaces HTTP %s without enrolling, retrying or discarding the user's selection",
    async (status, message) => {
      const fetchMock = mockFetch(settings(), async () =>
        jsonResponse({ detail: message }, status),
      );
      render(<ProactiveSettings />);
      const daily = await form("daily");
      fireEvent.change(
        daily.getByLabelText("Send only to my selected number"),
        {
          target: { value: "phone-backup" },
        },
      );
      fireEvent.change(daily.getByLabelText("Delivery timezone"), {
        target: { value: "Europe/London" },
      });
      fireEvent.click(daily.getByRole("button", { name: "Subscribe daily" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(daily.getByText("Off")).toBeVisible();
      expect(
        daily.getByLabelText("Send only to my selected number"),
      ).toHaveValue("phone-backup");
      expect(daily.getByLabelText("Delivery timezone")).toHaveValue(
        "Europe/London",
      );
      expect(
        daily.getByRole("button", { name: "Subscribe daily" }),
      ).toBeEnabled();
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(puts(fetchMock)).toHaveLength(1);
    },
  );

  it("disables the pending form so repeated clicks cannot create duplicate subscription writes", async () => {
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const fetchMock = mockFetch(settings(), () => pending);
    render(<ProactiveSettings />);
    const weekly = await form("weekly");
    fireEvent.change(weekly.getByLabelText("Send only to my selected number"), {
      target: { value: "phone-primary" },
    });
    fireEvent.click(weekly.getByRole("button", { name: "Subscribe weekly" }));
    const saving = weekly.getByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    for (const label of [
      "Send only to my selected number",
      "Delivery timezone",
      "Send at or after local hour",
      "Delivery day",
    ]) {
      expect(weekly.getByLabelText(label)).toBeDisabled();
    }
    fireEvent.click(saving);
    expect(puts(fetchMock)).toHaveLength(1);
    await act(async () =>
      finish(
        jsonResponse(
          settings([
            subscription("weekly", {
              id: "weekly-new",
              revision: 1,
              status: "active",
              effective: true,
              phone_identity_id: "phone-primary",
            }),
          ]),
        ),
      ),
    );
    expect(
      (await form("weekly")).getByRole("button", { name: "Save schedule" }),
    ).toBeEnabled();
  });

  it("keeps unavailable delivery visibly paused even when the user saves an active preference", async () => {
    const unavailable = subscription("daily", {
      id: "daily-existing",
      status: "active",
      revision: 6,
      phone_identity_id: "phone-primary",
      effective: false,
      delivery_available: false,
    });
    const fetchMock = mockFetch(settings([unavailable]), async () =>
      jsonResponse(settings([{ ...unavailable, revision: 7, local_hour: 9 }])),
    );
    render(<ProactiveSettings />);
    const daily = await form("daily");
    expect(
      daily.getByText("Delivery paused", { selector: ".badge" }),
    ).toBeVisible();
    expect(
      daily.getByText(/this does not activate WhatsApp delivery/),
    ).toBeVisible();
    expect(puts(fetchMock)).toHaveLength(0);
    fireEvent.change(daily.getByLabelText("Send at or after local hour"), {
      target: { value: "9" },
    });
    fireEvent.click(daily.getByRole("button", { name: "Save schedule" }));
    await waitFor(() =>
      expect(
        screen.getByLabelText("Send at or after local hour"),
      ).toBeEnabled(),
    );
    expect(
      screen.getByText("Delivery paused", { selector: ".badge" }),
    ).toBeVisible();
    expect(
      screen.queryByText("Active", { selector: ".badge" }),
    ).not.toBeInTheDocument();
    expect(JSON.parse(String(puts(fetchMock)[0][1]?.body))).toMatchObject({
      status: "active",
      expected_revision: 6,
      local_hour: 9,
    });
  });

  it("leaves paused schedules paused until the user explicitly subscribes again", async () => {
    const paused = subscription("weekly", {
      id: "weekly-paused",
      status: "paused",
      revision: 9,
      phone_identity_id: "phone-backup",
      local_hour: 10,
      weekday: 2,
    });
    const fetchMock = mockFetch(settings([paused]), async () =>
      jsonResponse(
        settings([
          { ...paused, status: "active", revision: 10, effective: true },
        ]),
      ),
    );
    render(<ProactiveSettings />);
    const weekly = await form("weekly");
    expect(weekly.getByText("Paused", { selector: ".badge" })).toBeVisible();
    expect(
      weekly.getByLabelText("Send only to my selected number"),
    ).toHaveValue("phone-backup");
    expect(puts(fetchMock)).toHaveLength(0);
    fireEvent.click(weekly.getByRole("button", { name: "Subscribe weekly" }));

    expect(
      await screen.findByText("Active", { selector: ".badge" }),
    ).toBeVisible();
    expect(JSON.parse(String(puts(fetchMock)[0][1]?.body))).toEqual({
      cadence: "weekly",
      status: "active",
      expected_revision: 9,
      phone_identity_id: "phone-backup",
      timezone: "Africa/Lagos",
      local_hour: 10,
      weekday: 2,
    });
  });

  it("shows a loading state and recovers from a failed settings read without any automatic write", async () => {
    let finish!: (response: Response) => void;
    const initial = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockReturnValueOnce(initial)
      .mockResolvedValueOnce(jsonResponse(settings()));
    render(<ProactiveSettings />);
    expect(screen.getByText("Loading content")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Subscribe daily" }),
    ).not.toBeInTheDocument();
    await act(async () =>
      finish(
        jsonResponse({ detail: "Settings are temporarily unavailable" }, 503),
      ),
    );
    expect(
      await screen.findByText("Settings are temporarily unavailable"),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Reload settings" }));

    expect((await form("daily")).getByText("Off")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(
      fetchMock.mock.calls.every(([, request]) => request?.method !== "PUT"),
    ).toBe(true);
  });
});
