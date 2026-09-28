/**
 * Railway Infrastructure as Code — optional, forward-looking config.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `railway.toml` / `railway.json` (Config as Code) are deprecated and stop
 * working on 2026-12-01. This file is the replacement. It is INERT until you
 * explicitly run it:
 *
 *     railway link
 *     railway config plan      # review the diff, change nothing
 *     railway config apply     # only once the plan looks right
 *
 * If you prefer the dashboard, ignore this file — it does not affect a normal
 * "connect repo -> deploy" flow.
 *
 * THE BUG THIS PREVENTS
 * ---------------------
 * This repository is a monorepo. Deploying with the default Root Directory
 * (`/`) makes Railpack look for a Python/Node manifest at the repository root,
 * find none, and fail with:
 *
 *     ⚠ Script start.sh not found
 *     ✖ Railpack could not determine how to build the app.
 *
 * `rootDirectory` below is the fix. Declaring it here makes that mistake
 * impossible when the config is applied, instead of relying on someone
 * remembering to click it in Settings -> Source.
 *
 * BEFORE YOU APPLY
 * ----------------
 * If the project already exists in Railway, import it first so this file
 * manages the real resources instead of creating duplicates:
 *
 *     railway config pull
 *
 * Then edit and apply.
 */
import { defineRailway, github, group, postgres, preserve, project, service } from "railway/iac";

// Change this if you forked the repository.
const REPO = "esmatshonam3/xray-panel";
const BRANCH = "main";

export default defineRailway(() => {
  const db = postgres("postgres");

  // ---------------------------------------------------------------- panel ---
  // Control plane: API, web UI, Telegram bot, scheduler.
  // `rootDirectory` makes Railway build panel/Dockerfile with panel/ as the
  // build context, which is exactly what that Dockerfile expects.
  const panel = service("xray-panel", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "panel" }),
    healthcheck: "/api/v1/health/live",
    healthcheckTimeout: 120,
    env: {
      DATABASE_URL: db.env.DATABASE_URL,
      // Self-reference: Railway expands this at deploy time.
      PANEL_BASE_URL: "https://${{RAILWAY_PUBLIC_DOMAIN}}",
      CORS_ORIGINS: "https://${{RAILWAY_PUBLIC_DOMAIN}}",
      ENVIRONMENT: "production",
      DEBUG: "false",
      ENABLE_SCHEDULER: "true",
      // Exactly one uvicorn worker keeps the in-process scheduler single-leader.
      WEB_CONCURRENCY: "1",
      BACKUP_ENABLED: "true",
      // Secrets stay as they are in Railway and are never written into git.
      SECRET_KEY: preserve(),
      ENCRYPTION_KEY: preserve(),
      SUPERADMIN_PASSWORD: preserve(),
      TELEGRAM_BOT_TOKEN: preserve(),
      TELEGRAM_BOT_USERNAME: preserve(),
      TELEGRAM_WEBHOOK_SECRET: preserve(),
      TELEGRAM_ADMIN_IDS: preserve(),
    },
  });

  // ----------------------------------------------------------- node agent ---
  // Data plane: runs xray-core. It needs a TCP Proxy for the inbound port
  // (Settings -> Networking) which is not expressible in this file.
  //
  // NOTE: this service is not strictly required. You can run the agent on any
  // VPS instead — see docs/DEPLOY_RAILWAY.md, section "بخش ۴".
  const node = service("xray-node", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "node-agent" }),
    healthcheck: "/health",
    healthcheckTimeout: 120,
    env: {
      AGENT_PORT: "8081",
      XRAY_API_PORT: "10085",
      XRAY_LOG_LEVEL: "warning",
      NODE_NAME: "railway-node-1",
      NODE_TOKEN: preserve(),
      PUBLIC_HOST: preserve(),
    },
  });

  const backend = group("Backend", [panel, node, db]);

  return project("xray-panel", {
    resources: [backend],
  });
});
