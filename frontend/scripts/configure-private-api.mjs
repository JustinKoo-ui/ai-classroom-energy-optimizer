// Run from frontend/. Tokens stay in backend/.env and Cloudflare Worker secrets.
import { randomBytes } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const frontend = fileURLToPath(new URL("../", import.meta.url));
const backendEnv = new URL("../../backend/.env", import.meta.url);
const config = JSON.parse(await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
if (config.name !== "nexergy") throw new Error("Unexpected Worker target; setup stopped.");
let text;
try { text = await readFile(backendEnv, "utf8"); } catch {
  throw new Error("Configure backend/.env with the existing database and MQTT settings first.");
}
const existing = text.match(/^CLASSROOM_API_PROXY_TOKEN\s*=\s*["']?([A-Za-z0-9_-]{32,})["']?\s*$/m);
const token = existing?.[1] ?? randomBytes(48).toString("base64url");
const enableRemoteControl = process.argv.includes("--enable-remote-control");
const operatorIndex = process.argv.indexOf("--operator-email");
const operatorEmail = operatorIndex >= 0 ? (process.argv[operatorIndex + 1] ?? "").trim().toLowerCase() : "";
if (enableRemoteControl && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(operatorEmail)) {
  throw new Error("Remote control setup requires --operator-email with an approved Cloudflare Access email.");
}
const updates = {
  CLASSROOM_API_PROXY_ENABLED: "true",
  CLASSROOM_API_PROXY_TOKEN: token,
  CLASSROOM_API_PROXY_CLASSROOM_ID: config.vars.CLASSROOM_API_CLASSROOM_ID,
  ...(enableRemoteControl ? {
    CLASSROOM_REMOTE_CONTROL_ENABLED: "true",
    CLASSROOM_REMOTE_CONTROL_ALLOWED_EMAILS: operatorEmail,
  } : {}),
};
for (const [key, value] of Object.entries(updates)) {
  const line = `${key}=${value}`;
  const pattern = new RegExp(`^${key}\\s*=.*$`, "m");
  text = pattern.test(text) ? text.replace(pattern, line) : `${text.trimEnd()}\n${line}\n`;
}
await writeFile(backendEnv, text, { mode: 0o600 });
const workerSecrets = {
  CLASSROOM_API_PROXY_TOKEN: token,
  ...(enableRemoteControl ? { CLASSROOM_REMOTE_CONTROL_ALLOWED_EMAILS: operatorEmail } : {}),
};
for (const [key, value] of Object.entries(workerSecrets)) {
  const result = spawnSync(process.execPath, [
    "node_modules/wrangler/bin/wrangler.js", "secret", "put", key,
    "--config", "wrangler.jsonc",
  ], {
    cwd: frontend,
    input: `${value}\n`,
    encoding: "utf8",
    env: { ...process.env, WRANGLER_WRITE_LOGS: "false" },
  });
  if (result.status !== 0) {
    const diagnostic = (result.stderr || result.stdout || "No CLI diagnostic returned.")
      .split(token).join("[REDACTED]").split(operatorEmail || "\0").join("[OPERATOR]").slice(-2200);
    console.error(diagnostic);
    throw new Error("Backend configured, but Cloudflare secret upload failed. Check wrangler login, then rerun this script.");
  }
}
console.log("Private bridge enabled in backend/.env; matching Worker secret stored without displaying its value.");
if (enableRemoteControl) console.log("Remote relay controls enabled for the configured Cloudflare operator.");
console.log("Reload the backend to use these settings, then build and deploy the frontend.");
