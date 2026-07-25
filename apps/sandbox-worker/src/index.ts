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
    const sandboxId = await scopedSandboxId(
      env.SANDBOX_SERVICE_TOKEN,
      tenantId,
      workspace,
    );
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
      if (operation === "document-pages") {
        return json(await extractDocumentPages(sandbox, body));
      }
      if (operation === "export") {
        return json(await exportFile(sandbox, body));
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
      result.stdout.length > MAX_TEXT_OUTPUT ||
      result.stderr.length > MAX_TEXT_OUTPUT,
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
    const result = await sandbox.writeFile(path, content, {
      encoding: "utf-8",
    });
    return {
      written: result.success,
      path,
      bytes_written:
        "bytesWritten" in result ? result.bytesWritten : content.length,
    };
  }
  if (action === "list") {
    const result = await sandbox.listFiles(path, {
      recursive: false,
      includeHidden: false,
    });
    return {
      path,
      files: result.files.slice(0, 200),
      truncated: result.files.length > 200,
    };
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
  await sandbox.writeFile("/workspace/.media/input", encoded, {
    encoding: "base64",
  });
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
    await sandbox.exec(
      "rm -f /workspace/.media/input /workspace/.media/frame-*.jpg",
      {
        cwd: "/workspace",
        timeout: 5_000,
        origin: "internal",
      },
    );
  }
}

async function extractDocumentPages(
  sandbox: SandboxBinding,
  body: JsonObject,
): Promise<JsonObject> {
  const encoded = stringValue(body, "content_base64", 24_000_000);
  if (
    body.mime_type !== "application/pdf" ||
    !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)
  ) {
    throw new RequestError("invalid_document", 400);
  }
  await sandbox.mkdir("/workspace/.media", { recursive: true });
  await sandbox.writeFile("/workspace/.media/input.pdf", encoded, {
    encoding: "base64",
  });
  try {
    const result = await sandbox.exec(
      "pdftoppm -f 1 -l 4 -jpeg -r 110 -scale-to 1400 " +
        "/workspace/.media/input.pdf /workspace/.media/page",
      { cwd: "/workspace", timeout: 30_000, origin: "internal" },
    );
    if (!result.success) {
      throw new RequestError("document_decode_failed", 422);
    }
    const pages: string[] = [];
    for (let index = 1; index <= 4; index += 1) {
      const path = `/workspace/.media/page-${index}.jpg`;
      try {
        const page = await sandbox.readFile(path, { encoding: "base64" });
        if (page.content.length <= 700_000) {
          pages.push(page.content);
        }
      } catch {
        break;
      }
    }
    if (pages.length === 0) {
      throw new RequestError("document_decode_failed", 422);
    }
    return { pages, mime_type: "image/jpeg", count: pages.length };
  } finally {
    await sandbox.exec(
      "rm -f /workspace/.media/input.pdf /workspace/.media/page-*.jpg",
      { cwd: "/workspace", timeout: 5_000, origin: "internal" },
    );
  }
}

async function exportFile(
  sandbox: SandboxBinding,
  body: JsonObject,
): Promise<JsonObject> {
  const path = workspacePath(stringValue(body, "path", 500));
  const mimeType = stringValue(body, "mime_type", 100);
  const textTypes = new Set(["application/json", "text/csv", "text/plain"]);
  if (textTypes.has(mimeType)) {
    const source = await sandbox.readFile(path, { encoding: "utf-8" });
    const size =
      source.size ?? new TextEncoder().encode(source.content).byteLength;
    if (size <= 0 || size > 2_000_000) {
      throw new RequestError("export_size_invalid", 422);
    }
    if (mimeType === "application/json") {
      try {
        JSON.parse(source.content);
      } catch {
        throw new RequestError("invalid_export_content", 422);
      }
    }
    return {
      content_base64: bytesToBase64(new TextEncoder().encode(source.content)),
      mime_type: mimeType,
      size_bytes: size,
    };
  }
  if (
    !["application/pdf", "image/jpeg", "image/png", "video/mp4"].includes(
      mimeType,
    )
  ) {
    throw new RequestError("invalid_export_type", 400);
  }
  await sandbox.mkdir("/workspace/.exports", { recursive: true });
  const suffix =
    mimeType === "application/pdf"
      ? "pdf"
      : mimeType === "video/mp4"
        ? "mp4"
        : mimeType === "image/png"
          ? "png"
          : "jpg";
  const output = `/workspace/.exports/sanitized.${suffix}`;
  try {
    if (mimeType === "application/pdf") {
      const inspection = await sandbox.exec(
        `pages=$(pdfinfo ${shellQuote(path)} | awk '/^Pages:/ {print $2}'); ` +
          'test "$pages" -ge 1 && test "$pages" -le 50',
        {
          cwd: "/workspace",
          timeout: 10_000,
          origin: "internal",
        },
      );
      if (!inspection.success) {
        throw new RequestError("export_decode_failed", 422);
      }
    }
    const command =
      mimeType === "application/pdf"
        ? `gs -q -dSAFER -dBATCH -dNOPAUSE -dPrinted ` +
          "-dPreserveAnnots=false -dPreserveMarkedContent=false " +
          "-sDEVICE=pdfwrite -dCompatibilityLevel=1.7 " +
          `-sOutputFile=${shellQuote(output)} ${shellQuote(path)}`
        : mimeType === "video/mp4"
          ? `ffmpeg -v error -y -i ${shellQuote(path)} -map_metadata -1 ` +
            "-t 60 -vf 'scale=1280:-2:force_original_aspect_ratio=decrease' " +
            "-c:v libx264 -preset veryfast -crf 28 -c:a aac -b:a 96k " +
            "-movflags +faststart " +
            shellQuote(output)
          : `ffmpeg -v error -y -i ${shellQuote(path)} -map_metadata -1 ` +
            "-frames:v 1 -vf 'scale=1600:-2:force_original_aspect_ratio=decrease' " +
            (mimeType === "image/png" ? "-c:v png " : "-q:v 5 ") +
            shellQuote(output);
    const result = await sandbox.exec(command, {
      cwd: "/workspace",
      timeout: 30_000,
      origin: "internal",
    });
    if (!result.success) {
      throw new RequestError("export_decode_failed", 422);
    }
    const exported = await sandbox.readFile(output, { encoding: "base64" });
    const size = exported.size ?? base64DecodedSize(exported.content);
    if (size <= 0 || size > 8_388_608) {
      throw new RequestError("export_size_invalid", 422);
    }
    return {
      content_base64: exported.content,
      mime_type: mimeType,
      size_bytes: size,
    };
  } finally {
    await sandbox.exec("rm -f /workspace/.exports/sanitized.*", {
      cwd: "/workspace",
      timeout: 5_000,
      origin: "internal",
    });
  }
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

function bytesToBase64(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64DecodedSize(value: string): number {
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(value) || value.length % 4 !== 0) {
    throw new RequestError("invalid_export_content", 422);
  }
  const padding = value.endsWith("==") ? 2 : value.endsWith("=") ? 1 : 0;
  return (value.length / 4) * 3 - padding;
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
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return hex(new Uint8Array(digest)).slice(0, 16);
}

function hex(value: Uint8Array): string {
  return Array.from(value, (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
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
