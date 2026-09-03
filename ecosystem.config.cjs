const { existsSync } = require("fs");
const { join } = require("path");

const root = __dirname;
const python = existsSync(join(root, "venv", "Scripts", "python.exe"))
  ? join(root, "venv", "Scripts", "python.exe")
  : join(root, "venv", "bin", "python");

const host = process.env.APP_HOST || "127.0.0.1";
const port = process.env.APP_PORT || "8000";

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
        "*",
      ],
      interpreter: "none",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      watch: false,
      max_restarts: 10,
      min_uptime: "10s",
      max_memory_restart: "1G",
      kill_timeout: 8000,
      env: {
        PYTHONUNBUFFERED: "1",
      },
      error_file: join(root, "logs", "pm2-error.log"),
      out_file: join(root, "logs", "pm2-out.log"),
      merge_logs: true,
      time: true,
    },
  ],
};
