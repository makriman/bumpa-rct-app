import { createServer } from "node:http";

const fixturePort = 3099;
const expectedCookie = "bb_session=e2e-session-fixture";
const fixturePhone = "+12025550123";
const fixtureCode = "246810";
const fixtureConversationId = "conversation-e2e";
const fixtureMessages = [];
const session = {
  user: {
    id: "user-e2e",
    name: "E2E Operator Owner",
    email: "e2e@example.com",
    phone_e164: fixturePhone,
  },
  platform_roles: [],
  memberships: [
    {
      id: "membership-e2e",
      tenant_id: "tenant-e2e",
      role: "owner",
      status: "active",
    },
  ],
  current_tenant_id: "tenant-e2e",
};

async function readJson(request) {
  let body = "";
  for await (const chunk of request) body += chunk.toString();
  try {
    return body ? JSON.parse(body) : {};
  } catch {
    return null;
  }
}

function json(response, status, body, headers = {}) {
  response.writeHead(status, {
    "content-type": "application/json",
    "cache-control": "no-store",
    ...headers,
  });
  response.end(JSON.stringify(body));
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1");
  const authorised = (request.headers.cookie ?? "")
    .split(";")
    .map((value) => value.trim())
    .includes(expectedCookie);

  if (request.method === "POST" && url.pathname === "/v1/auth/request-otp") {
    const payload = await readJson(request);
    if (payload?.phone_e164 !== fixturePhone) {
      json(response, 401, { detail: "Unknown isolated test identity" });
      return;
    }
    json(response, 202, {
      delivery: "web_pin",
      expires_in_seconds: 600,
      dev_code: null,
    });
    return;
  }

  if (request.method === "POST" && url.pathname === "/v1/auth/verify-otp") {
    const payload = await readJson(request);
    if (payload?.phone_e164 !== fixturePhone || payload?.code !== fixtureCode) {
      json(response, 401, { detail: "Invalid isolated test credentials" });
      return;
    }
    json(
      response,
      200,
      { message: "Verified", access_token: "fixture-token-not-forwarded" },
      {
        "set-cookie":
          "bb_session=e2e-session-fixture; HttpOnly; Path=/; SameSite=Lax; Max-Age=3600",
      },
    );
    return;
  }

  if (
    request.method === "GET" &&
    url.pathname === "/v1/auth/me" &&
    authorised
  ) {
    json(response, 200, session);
    return;
  }

  if (
    request.method === "GET" &&
    url.pathname === "/v1/chat/conversations/page" &&
    authorised
  ) {
    json(response, 200, {
      items: fixtureMessages.length
        ? [
            {
              id: fixtureConversationId,
              title: "A test business question",
              channel: "web",
              updated_at: fixtureMessages.at(-1).created_at,
              last_message_preview: fixtureMessages.at(-1).content,
            },
          ]
        : [],
      next_cursor: null,
    });
    return;
  }

  if (
    request.method === "GET" &&
    url.pathname ===
      `/v1/chat/conversations/${fixtureConversationId}/messages` &&
    authorised
  ) {
    json(response, 200, { items: fixtureMessages, next_cursor: null });
    return;
  }

  if (
    request.method === "POST" &&
    url.pathname === "/v1/chat/web" &&
    authorised
  ) {
    const payload = await readJson(request);
    if (!payload?.message || !payload?.client_message_id) {
      json(response, 422, {
        detail: "A message and client message ID are required",
      });
      return;
    }
    const sequence = fixtureMessages.length + 1;
    const createdAt = new Date().toISOString();
    const inboundId = `fixture-inbound-${sequence}`;
    const outboundId = `fixture-outbound-${sequence + 1}`;
    const answer =
      "Your isolated preview is connected. In production, Bestie answers from the authenticated workspace data available to this business.";
    fixtureMessages.push(
      {
        id: inboundId,
        direction: "inbound",
        content: payload.message,
        created_at: createdAt,
      },
      {
        id: outboundId,
        direction: "outbound",
        content: answer,
        created_at: createdAt,
      },
    );
    json(response, 200, {
      conversation_id: fixtureConversationId,
      inbound_message_id: inboundId,
      outbound_message_id: outboundId,
      answer,
      data_freshness: null,
    });
    return;
  }

  json(response, 401, { detail: "Unauthorised test fixture request" });
});

server.listen(fixturePort, "127.0.0.1");

const close = () => server.close(() => process.exit(0));
process.on("SIGINT", close);
process.on("SIGTERM", close);
