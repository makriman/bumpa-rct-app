import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";

const appContracts = {
  web: {
    forbiddenRoutePrefixes: ["/admin", "/research"],
    requiredRoutes: ["/page", "/chat/page", "/chat/[conversationId]/page"],
    forbiddenClientPatterns: [
      /admin\.bumpabestie\.com/i,
      /research\.bumpabestie\.com/i,
      /Platform operations/i,
      /Research access/i,
      /Research workflows/i,
    ],
  },
  "admin-web": {
    forbiddenRoutePrefixes: ["/chat", "/research"],
    requiredRoutes: ["/page", "/tenants/page", "/connections/page"],
    forbiddenClientPatterns: [],
  },
  "research-web": {
    forbiddenRoutePrefixes: ["/admin", "/chat"],
    requiredRoutes: ["/page", "/questions/page", "/conversations/page"],
    forbiddenClientPatterns: [],
  },
};

const forbiddenFixturePatterns = [
  /NEXT_PUBLIC_DEMO_MODE/i,
  /demoFallbackEnabled/i,
  /deterministic fixtures/i,
  /Local preview/i,
  /Amara Okafor/i,
  /Tobi Adeyemi/i,
  /Demo superadmin/i,
  /Demo operator/i,
  /Demo researcher/i,
  /@example\.test/i,
];

async function filesBelow(directory) {
  const files = [];
  for (const entry of await readdir(directory)) {
    const candidate = path.join(directory, entry);
    if ((await stat(candidate)).isDirectory()) {
      files.push(...(await filesBelow(candidate)));
    } else {
      files.push(candidate);
    }
  }
  return files;
}

const failures = [];

for (const [app, contract] of Object.entries(appContracts)) {
  const buildRoot = path.join("apps", app, ".next");
  const staticFiles = (await filesBelow(path.join(buildRoot, "static"))).filter(
    (file) => file.endsWith(".js") || file.endsWith(".css"),
  );
  const patterns = [
    ...forbiddenFixturePatterns,
    ...contract.forbiddenClientPatterns,
  ];

  for (const file of staticFiles) {
    const contents = await readFile(file, "utf8");
    for (const pattern of patterns) {
      if (pattern.test(contents)) {
        failures.push(`${app}: ${file} contains forbidden ${pattern}`);
      }
    }
  }

  const manifestPath = path.join(
    buildRoot,
    "server",
    "app-paths-manifest.json",
  );
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const routes = Object.keys(manifest);
  for (const prefix of contract.forbiddenRoutePrefixes) {
    const leaked = routes.filter((route) => route.startsWith(prefix));
    if (leaked.length) {
      failures.push(`${app}: forbidden routes emitted: ${leaked.join(", ")}`);
    }
  }
  for (const required of contract.requiredRoutes) {
    if (!routes.includes(required)) {
      failures.push(`${app}: required route missing from build: ${required}`);
    }
  }

  console.log(
    `${app}: ${staticFiles.length} client assets and ${routes.length} routes checked`,
  );
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exitCode = 1;
} else {
  console.log("Frontend bundle and route isolation contract passed");
}
