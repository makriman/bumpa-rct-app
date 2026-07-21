import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { once } from "node:events";
import lighthouse, { desktopConfig } from "lighthouse";
import * as chromeLauncher from "chrome-launcher";

const baseUrl = "http://127.0.0.1:3010";
const outputDirectory = new URL("../lighthouse-reports/", import.meta.url);
const routes = [
  { name: "landing", path: "/" },
  { name: "login", path: "/login" },
];
const runCount = 3;
const budgets = {
  accessibility: { minimum: 0.95, label: "accessibility score" },
  performance: { minimum: 0.9, label: "performance score" },
  lcp: { maximum: 2_500, label: "largest contentful paint (ms)" },
  cls: { maximum: 0.1, label: "cumulative layout shift" },
  tbt: { maximum: 200, label: "total blocking time (ms)" },
};

const delay = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitForServer(server) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (server.exitCode !== null) {
      throw new Error(
        `Next.js exited before becoming ready (${server.exitCode}).`,
      );
    }
    try {
      const response = await fetch(`${baseUrl}/api/health`, {
        signal: AbortSignal.timeout(1_000),
      });
      if (response.ok) return;
    } catch {}
    await delay(500);
  }
  throw new Error("Next.js did not become ready for Lighthouse within 60s.");
}

function median(values) {
  const ordered = [...values].sort((left, right) => left - right);
  return ordered[Math.floor(ordered.length / 2)];
}

function measurement(lhr) {
  return {
    accessibility: lhr.categories.accessibility.score ?? 0,
    performance: lhr.categories.performance.score ?? 0,
    lcp: lhr.audits["largest-contentful-paint"].numericValue ?? Infinity,
    cls: lhr.audits["cumulative-layout-shift"].numericValue ?? Infinity,
    tbt: lhr.audits["total-blocking-time"].numericValue ?? Infinity,
  };
}

function validate(routeName, values) {
  const failures = [];
  for (const [metric, budget] of Object.entries(budgets)) {
    const value = values[metric];
    if ("minimum" in budget && value < budget.minimum) {
      failures.push(
        `${routeName}: ${budget.label} ${value} is below ${budget.minimum}`,
      );
    }
    if ("maximum" in budget && value > budget.maximum) {
      failures.push(
        `${routeName}: ${budget.label} ${value} exceeds ${budget.maximum}`,
      );
    }
  }
  return failures;
}

await mkdir(outputDirectory, { recursive: true });
const server = spawn("npm", ["run", "start", "--", "-H", "127.0.0.1"], {
  cwd: new URL("../", import.meta.url),
  env: {
    ...process.env,
    HOSTNAME: "127.0.0.1",
    PORT: "3010",
  },
  stdio: ["ignore", "pipe", "pipe"],
});
let serverOutput = "";
server.stdout.on("data", (chunk) => {
  serverOutput += chunk.toString();
});
server.stderr.on("data", (chunk) => {
  serverOutput += chunk.toString();
});

let chrome;
try {
  await waitForServer(server);
  chrome = await chromeLauncher.launch({
    chromeFlags: ["--headless=new", "--no-sandbox", "--disable-gpu"],
  });
  const summary = {};
  const failures = [];
  for (const route of routes) {
    const runs = [];
    for (let index = 0; index < runCount; index += 1) {
      const result = await lighthouse(
        `${baseUrl}${route.path}`,
        {
          port: chrome.port,
          output: "json",
          logLevel: "error",
          onlyCategories: ["performance", "accessibility"],
          screenEmulation: {
            mobile: false,
            width: 1440,
            height: 900,
            deviceScaleFactor: 1,
            disabled: false,
          },
        },
        desktopConfig,
      );
      if (!result)
        throw new Error(`Lighthouse returned no ${route.name} result.`);
      runs.push(measurement(result.lhr));
      await writeFile(
        new URL(`${route.name}-${index + 1}.json`, outputDirectory),
        result.report,
      );
    }
    const aggregate = Object.fromEntries(
      Object.keys(budgets).map((metric) => [
        metric,
        median(runs.map((run) => run[metric])),
      ]),
    );
    summary[route.name] = { aggregate, runs };
    failures.push(...validate(route.name, aggregate));
  }
  await writeFile(
    new URL("summary.json", outputDirectory),
    `${JSON.stringify({ budgets, routes: summary }, null, 2)}\n`,
  );
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
  if (failures.length) throw new Error(failures.join("\n"));
} catch (error) {
  if (serverOutput) process.stderr.write(serverOutput);
  throw error;
} finally {
  if (chrome) await chrome.kill();
  server.kill("SIGTERM");
  await Promise.race([once(server, "exit"), delay(5_000)]);
  if (server.exitCode === null) server.kill("SIGKILL");
}
