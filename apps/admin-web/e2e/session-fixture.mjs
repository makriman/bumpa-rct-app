import { createServer } from "node:http";

const port = 3100;
const fixturePhone = "+12025550124";
const fixtureCode = "246811";
const sessionCookie = "bb_session=e2e-admin-session";

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
        "set-cookie": `${sessionCookie}; HttpOnly; Path=/; SameSite=Lax; Max-Age=3600`,
      },
    );
    return;
  }
  if (request.method !== "GET" || url.pathname !== "/v1/auth/me") {
    json(response, 404, { detail: "Unknown isolated fixture route" });
    return;
  }

  const cookies = (request.headers.cookie ?? "")
    .split(";")
    .map((value) => value.trim());
  if (cookies.includes(sessionCookie)) {
    json(response, 200, {
      user: {
        id: "admin-e2e",
        name: "E2E platform operator",
        email: null,
        phone_e164: "+12025550124",
      },
      platform_roles: ["operator", "superadmin"],
      memberships: [],
      current_tenant_id: null,
    });
    return;
  }
  if (cookies.includes("bb_session=e2e-wrong-role")) {
    json(response, 200, {
      user: {
        id: "research-e2e",
        name: "E2E researcher",
        email: null,
        phone_e164: "+12025550125",
      },
      platform_roles: ["researcher"],
      memberships: [],
      current_tenant_id: null,
    });
    return;
  }
  json(response, 401, { detail: "Unauthorised isolated fixture request" });
});

server.listen(port, "127.0.0.1");

const close = () => server.close(() => process.exit(0));
process.on("SIGINT", close);
process.on("SIGTERM", close);
