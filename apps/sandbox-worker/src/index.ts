import {
  ContainerProxy,
  Sandbox as CloudflareSandbox,
  getSandbox,
  type Sandbox as SandboxBinding,
} from "@cloudflare/sandbox";
import {
  RequestError,
  timeoutValue,
  workspacePath,
  type JsonObject,
} from "./security";
import { generateImage } from "./managed-image";

export { ContainerProxy };

interface Env {
  Sandbox: DurableObjectNamespace<SandboxBinding>;
  SANDBOX_SERVICE_TOKEN: string;
  AI: Ai;
}

const MAX_JSON_BYTES = 25 * 1024 * 1024;
const MAX_TEXT_OUTPUT = 100_000;
const WORKSPACE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const TENANT_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{7,79}$/;

export class Sandbox extends CloudflareSandbox<Env> {
  enableInternet = false;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ status: "ok", internet_access: false });
    }
    if (!(await authenticated(request, env))) {
      return json({ error: "unauthorized" }, 401);
    }
    const tenantId = request.headers.get("x-bumpa-tenant-id") ?? "";
    if (!TENANT_PATTERN.test(tenantId)) {
      return json({ error: "invalid_tenant" }, 403);
    }
    if (request.method === "POST" && url.pathname === "/v1/media/image") {
      try {
        return json(await generateImage(env.AI, await boundedJson(request)));
      } catch (error) {
        if (error instanceof RequestError) {
          return json({ error: error.code }, error.status);
        }
        return json({ error: "image_provider_unavailable" }, 503);
      }
    }
    const match = /^\/v1\/sandboxes\/([^/]+)(?:\/([^/]+))?$/.exec(url.pathname);
    if (!match) {
      return json({ error: "not_found" }, 404);
    }
    const workspace = decodeURIComponent(match[1] ?? "");
    const operation = match[2] ?? "";
    if (!WORKSPACE_PATTERN.test(workspace)) {
      return json({ error: "invalid_workspace" }, 400);
    }
    const sandboxId = await scopedSandboxId(env.SANDBOX_SERVICE_TOKEN, tenantId, workspace);
    const sandbox = getSandbox(env.Sandbox, sandboxId, {
      sleepAfter: "10m",
      enableDefaultSession: false,
      normalizeId: true,
      transport: "rpc",
      labels: {
        tenant: await shortHash(tenantId),
        workload: "bumpa-bestie-agent",
      },
    });

    try {
      if (request.method === "DELETE" && !operation) {
        await sandbox.destroy();
        return json({ destroyed: true });
      }
      if (request.method !== "POST") {
        return json({ error: "method_not_allowed" }, 405);
      }
      const body = await boundedJson(request);
      if (operation === "code") {
        return json(await runCode(sandbox, body));
      }
      if (operation === "exec") {
        return json(await runCommand(sandbox, body));
      }
      if (operation === "files") {
        return json(await fileOperation(sandbox, body));
      }
      if (operation === "video-frames") {
        return json(await extractVideoFrames(sandbox, body));
      }
      return json({ error: "not_found" }, 404);
    } catch (error) {
      if (error instanceof RequestError) {
        return json({ error: error.code }, error.status);
      }
      return json({ error: "sandbox_unavailable" }, 503);
    }
  },
} satisfies ExportedHandler<Env>;

async function runCode(
  sandbox: SandboxBinding,
  body: JsonObject,
): Promise<JsonObject> {
  const language = stringValue(body, "language", 20);
  if (!["python", "javascript", "typescript"].includes(language)) {
    throw new RequestError("invalid_language", 400);
  }
  const code = stringValue(body, "code", 50_000);
  const timeout = timeoutValue(body);
  const result = await sandbox.runCode(code, {
    language: language as "python" | "javascript" | "typescript",
    timeout,
  });
  return {
    success: result.error === undefined,
    stdout: boundedLines(result.logs.stdout),
    stderr: boundedLines(result.logs.stderr),
    results: result.results.slice(0, 20).map((item) => ({
      text: boundedOptional(item.text),
      markdown: boundedOptional(item.markdown),
      json: boundedJsonValue(item.json),
    })),
    error: result.error
      ? {
          name: String(result.error.name).slice(0, 100),
          message: String(result.error.message).slice(0, 1_000),
        }
      : null,
  };
}

async function runCommand(
  sandbox: SandboxBinding,
  body: JsonObject,
): Promise<JsonObject> {
  const command = stringValue(body, "command", 8_000);
  const cwd = workspacePath(stringValue(body, "cwd", 500));
  const result = await sandbox.exec(command, {
    cwd,
    timeout: timeoutValue(body),
    env: {
      HOME: "/workspace",
      TMPDIR: "/workspace/.tmp",
      NO_PROXY: "localhost,127.0.0.1",
    },
  });
  return {
    success: result.success,
    exit_code: result.exitCode,
    stdout: result.stdout.slice(0, MAX_TEXT_OUTPUT),
    stderr: result.stderr.slice(0, MAX_TEXT_OUTPUT),
    duration_ms: result.duration,
    truncated:
      result.stdout.length > MAX_TEXT_OUTPUT || result.stderr.length > MAX_TEXT_OUTPUT,
  };
}

async function fileOperation(
  sandbox: SandboxBinding,
  body: JsonObject,
): Promise<JsonObject> {
  const action = stringValue(body, "action", 20);
  const path = workspacePath(stringValue(body, "path", 500));
  if (action === "read") {
    const result = await sandbox.readFile(path, { encoding: "utf-8" });
    return {
      path,
      content: result.content.slice(0, 250_000),
      truncated: result.content.length > 250_000,
      size: result.size,
      mime_type: result.mimeType,
    };
  }
  if (action === "write") {
    const content = stringValue(body, "content", 250_000, true);
    const result = await sandbox.writeFile(path, content, { encoding: "utf-8" });
    return {
      written: result.success,
      path,
      bytes_written: "bytesWritten" in result ? result.bytesWritten : content.length,
    };
  }
  if (action === "list") {
    const result = await sandbox.listFiles(path, {
      recursive: false,
      includeHidden: false,
    });
    return { path, files: result.files.slice(0, 200), truncated: result.files.length > 200 };
  }
  if (action === "delete") {
    const result = await sandbox.deleteFile(path);
    return { deleted: result.success, path };
  }
  throw new RequestError("invalid_file_action", 400);
}

async function extractVideoFrames(
  sandbox: SandboxBinding,
  body: JsonObject,
): Promise<JsonObject> {
  const encoded = stringValue(body, "content_base64", 24_000_000);
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) {
    throw new RequestError("invalid_video", 400);
  }
  await sandbox.mkdir("/workspace/.media", { recursive: true });
  await sandbox.writeFile("/workspace/.media/input", encoded, { encoding: "base64" });
  try {
    const result = await sandbox.exec(
      "ffmpeg -v error -i /workspace/.media/input " +
        "-vf 'fps=1/5,scale=512:-2' -frames:v 3 -q:v 6 " +
        "/workspace/.media/frame-%02d.jpg",
      { cwd: "/workspace", timeout: 30_000 },
    );
    if (!result.success) {
      throw new RequestError("video_decode_failed", 422);
    }
    const frames: string[] = [];
    for (let index = 1; index <= 3; index += 1) {
      const path = `/workspace/.media/frame-${String(index).padStart(2, "0")}.jpg`;
      try {
        const frame = await sandbox.readFile(path, { encoding: "base64" });
        if (frame.content.length <= 350_000) {
          frames.push(frame.content);
        }
      } catch {
        break;
      }
    }
    if (frames.length === 0) {
      throw new RequestError("video_decode_failed", 422);
    }
    return { frames, mime_type: "image/jpeg", count: frames.length };
  } finally {
    await sandbox.exec("rm -f /workspace/.media/input /workspace/.media/frame-*.jpg", {
      cwd: "/workspace",
      timeout: 5_000,
      origin: "internal",
    });
  }
}

async function boundedJson(request: Request): Promise<JsonObject> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > MAX_JSON_BYTES) {
    throw new RequestError("request_too_large", 413);
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > MAX_JSON_BYTES) {
    throw new RequestError("request_too_large", 413);
  }
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("object required");
    }
    return value as JsonObject;
  } catch {
    throw new RequestError("invalid_json", 400);
  }
}

function stringValue(
  body: JsonObject,
  key: string,
  maximum: number,
  allowEmpty = false,
): string {
  const value = body[key];
  if (
    typeof value !== "string" ||
    (!allowEmpty && value.length === 0) ||
    value.length > maximum ||
    value.includes("\0")
  ) {
    throw new RequestError(`invalid_${key}`, 400);
  }
  return value;
}

async function authenticated(request: Request, env: Env): Promise<boolean> {
  const header = request.headers.get("authorization") ?? "";
  const supplied = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!supplied || !env.SANDBOX_SERVICE_TOKEN) return false;
  const [left, right] = await Promise.all([
    crypto.subtle.digest("SHA-256", new TextEncoder().encode(supplied)),
    crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(env.SANDBOX_SERVICE_TOKEN),
    ),
  ]);
  const leftBytes = new Uint8Array(left);
  const rightBytes = new Uint8Array(right);
  let difference = leftBytes.length ^ rightBytes.length;
  for (let index = 0; index < leftBytes.length; index += 1) {
    difference |= leftBytes[index]! ^ rightBytes[index]!;
  }
  return difference === 0;
}

async function scopedSandboxId(
  secret: string,
  tenantId: string,
  workspace: string,
): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`${tenantId}\0${workspace}`),
  );
  return `bb-${hex(new Uint8Array(signature)).slice(0, 48)}`;
}

async function shortHash(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return hex(new Uint8Array(digest)).slice(0, 16);
}

function hex(value: Uint8Array): string {
  return Array.from(value, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function boundedLines(values: string[]): string[] {
  let remaining = MAX_TEXT_OUTPUT;
  const result: string[] = [];
  for (const value of values) {
    if (remaining <= 0) break;
    result.push(value.slice(0, remaining));
    remaining -= value.length;
  }
  return result;
}

function boundedOptional(value: string | undefined): string | null {
  return typeof value === "string" ? value.slice(0, 20_000) : null;
}

function boundedJsonValue(value: unknown): unknown {
  if (value === undefined) return null;
  try {
    const serialized = JSON.stringify(value);
    return serialized.length <= 20_000 ? value : { truncated: true };
  } catch {
    return null;
  }
}

function json(value: JsonObject, status = 200): Response {
  return Response.json(value, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
