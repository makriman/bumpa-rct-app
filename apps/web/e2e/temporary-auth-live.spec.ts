import { execFileSync } from "node:child_process";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

const pin = requiredEnvironment("TEMPORARY_AUTH_E2E_PIN");
const composeEnvFile = requiredEnvironment("TEMPORARY_AUTH_E2E_ENV_FILE");
const repositoryRoot = requiredEnvironment(
  "TEMPORARY_AUTH_E2E_REPOSITORY_ROOT",
);
requiredEnvironment("COMPOSE_PROJECT_NAME");
if (!/^\d{6}$/.test(pin)) {
  throw new Error("TEMPORARY_AUTH_E2E_PIN must contain exactly six digits");
}

function postgresScalar(sql: string): string {
  return execFileSync(
    "docker",
    [
      "compose",
      "--env-file",
      composeEnvFile,
      "exec",
      "-T",
      "postgres",
      "psql",
      "-v",
      "ON_ERROR_STOP=1",
      "-U",
      "bumpabestie",
      "-d",
      "bumpabestie",
      "-Atqc",
      sql,
    ],
    { cwd: repositoryRoot, encoding: "utf8" },
  ).trim();
}

async function enterPhone(page: Page, country: string, nationalNumber: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: /Country code/ }).click();
  await page.locator(`[role="option"][data-country-iso="${country}"]`).click();
  await page.getByLabel("Mobile number").fill(nationalNumber);
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(
    page.getByRole("heading", { name: "Enter your web PIN." }),
  ).toBeVisible();
  await expect(page.getByText(/no WhatsApp message was sent/i)).toBeVisible();
}

async function requestChallenge(request: APIRequestContext, phone: string) {
  return request.post("/api/backend/auth/request-otp", {
    data: { phone_e164: phone },
  });
}

async function verifyChallenge(request: APIRequestContext, phone: string) {
  return request.post("/api/backend/auth/verify-otp", {
    data: { phone_e164: phone, code: pin },
  });
}

test("real temporary PIN flow is mapped-only, provider-free, and revocable", async ({
  page,
}) => {
  expect(postgresScalar("SELECT count(*) FROM whatsapp_messages")).toBe("0");

  await enterPhone(page, "US", "202 555 0198");
  await page.getByLabel("Six-digit web PIN").fill(pin);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByText("Invalid or expired code", { exact: true }),
  ).toBeVisible();

  await page.context().clearCookies();
  await enterPhone(page, "NG", "0801 234 5679");
  const wrongPin = `${(Number(pin[0]) + 1) % 10}${pin.slice(1)}`;
  await page.getByLabel("Six-digit web PIN").fill(wrongPin);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByText("Invalid or expired code", { exact: true }),
  ).toBeVisible();

  await page.context().clearCookies();
  await enterPhone(page, "NG", "0801 234 5678");
  await expect(page.getByLabel("Six-digit web PIN")).toHaveAttribute(
    "type",
    "password",
  );
  await page.getByLabel("Six-digit web PIN").fill(pin);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(
    page.getByRole("textbox", { name: "Message Bumpa Bestie" }),
  ).toBeVisible();

  expect(
    postgresScalar(
      "SELECT count(*) FROM otp_sessions WHERE purpose = 'temporary_web_pin' " +
        "AND phone_e164 = '+2348012345678'",
    ),
  ).toBe("1");
  expect(
    postgresScalar(
      "SELECT count(*) FROM otp_sessions WHERE purpose = 'temporary_web_pin' " +
        "AND phone_e164 = '+2348012345679' AND consumed_at IS NULL",
    ),
  ).toBe("1");
  expect(
    postgresScalar("SELECT count(*) FROM otp_sessions WHERE purpose = 'login'"),
  ).toBe("0");
  expect(postgresScalar("SELECT count(*) FROM whatsapp_messages")).toBe("0");

  expect(
    postgresScalar(
      "WITH updated AS (UPDATE phone_identities SET opt_out = true " +
        "WHERE phone_e164 = '+2348012345678' RETURNING 1) " +
        "SELECT count(*) FROM updated",
    ),
  ).toBe("1");
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login\?next=%2Fchat$/);
});

test("PostgreSQL serializes parallel challenge creation and consumption", async ({
  request,
}) => {
  const phone = "+2348012345679";
  expect(
    postgresScalar(
      "WITH deleted AS (DELETE FROM otp_sessions " +
        "WHERE phone_e164 = '+2348012345679' " +
        "AND purpose = 'temporary_web_pin' RETURNING 1) " +
        "SELECT count(*) FROM deleted",
    ),
  ).toMatch(/^\d+$/);
  expect(
    postgresScalar(
      "WITH deleted AS (DELETE FROM auth_sessions WHERE user_id = " +
        "(SELECT id FROM users WHERE primary_phone_e164 = '+2348012345679') " +
        "RETURNING 1) SELECT count(*) FROM deleted",
    ),
  ).toMatch(/^\d+$/);

  const requestResponses = await Promise.all([
    requestChallenge(request, phone),
    requestChallenge(request, phone),
  ]);
  expect(requestResponses.map((response) => response.status())).toEqual([
    202, 202,
  ]);
  expect(
    postgresScalar(
      "SELECT count(*) FROM otp_sessions WHERE phone_e164 = '+2348012345679' " +
        "AND purpose = 'temporary_web_pin' AND consumed_at IS NULL",
    ),
  ).toBe("1");
  expect(
    postgresScalar(
      "SELECT count(*) FROM otp_sessions WHERE phone_e164 = '+2348012345679' " +
        "AND purpose = 'temporary_web_pin'",
    ),
  ).toBe("1");

  const verifyResponses = await Promise.all([
    verifyChallenge(request, phone),
    verifyChallenge(request, phone),
  ]);
  expect(verifyResponses.map((response) => response.status()).sort()).toEqual([
    200, 401,
  ]);
  expect(
    postgresScalar(
      "SELECT count(*) FROM otp_sessions WHERE phone_e164 = '+2348012345679' " +
        "AND purpose = 'temporary_web_pin' AND consumed_at IS NOT NULL",
    ),
  ).toBe("1");
  expect(
    postgresScalar(
      "SELECT count(*) FROM otp_sessions WHERE phone_e164 = '+2348012345679' " +
        "AND purpose = 'temporary_web_pin' AND consumed_at IS NULL",
    ),
  ).toBe("0");
  expect(
    postgresScalar(
      "SELECT count(*) FROM auth_sessions WHERE user_id = " +
        "(SELECT id FROM users WHERE primary_phone_e164 = '+2348012345679')",
    ),
  ).toBe("1");
});
