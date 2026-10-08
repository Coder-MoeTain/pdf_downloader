const { existsSync, readFileSync } = require("fs");
const { join } = require("path");

const root = __dirname;
const envFile = join(root, ".env");
if (existsSync(envFile)) {
  for (const line of readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 1) continue;
    const key = trimmed.slice(0, eq).trim();
    if (!key || process.env[key] !== undefined) continue;
    let value = trimmed.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}
const python = existsSync(join(root, "venv", "Scripts", "python.exe"))
  ? join(root, "venv", "Scripts", "python.exe")
  : join(root, "venv", "bin", "python");

const host = process.env.APP_HOST || "127.0.0.1";
const port = process.env.APP_PORT || "8000";
const trustedProxies = process.env.TRUSTED_PROXY_IPS || "127.0.0.1";

module.exports = {
  apps: [
    {
      name: "researchpaper",
      cwd: root,
      script: python,
      args: [
        "-m",
        "uvicorn",
        "app.web:app",
        "--host",
        host,
        "--port",
        port,
        "--proxy-headers",
        "--forwarded-allow-ips",
        trustedProxies,
      ],
      interpreter: "none",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      watch: false,
      max_restarts: 10,
      min_uptime: "10s",
      // Do not set max_memory_restart: PM2 checks every ~30s and was killing this
      // Python app in a loop (RSS often exceeds 1G with downloads / libraries).
      kill_timeout: 12000,
      listen_timeout: 30000,
      shutdown_with_message: true,
      env: {
        PYTHONUNBUFFERED: "1",
        ...(process.env.SESSION_SECRET ? { SESSION_SECRET: process.env.SESSION_SECRET } : {}),
        ...(process.env.APP_ENV ? { APP_ENV: process.env.APP_ENV } : {}),
      },
      error_file: join(root, "logs", "pm2-error.log"),
      out_file: join(root, "logs", "pm2-out.log"),
      merge_logs: true,
      time: true,
    },
  ],
};
