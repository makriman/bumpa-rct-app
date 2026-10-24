import { readFileSync, writeFileSync } from "node:fs";

// Wrangler emits trailing spaces in a few generated comments. Keep its output
// reproducible without making every typecheck dirty the checked-in bindings.
const target = new URL("../worker-configuration.d.ts", import.meta.url);
writeFileSync(target, readFileSync(target, "utf8").replace(/[\t ]+$/gm, ""));
