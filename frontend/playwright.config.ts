import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 180000,
  workers: 1,
  expect: { timeout: 60000 },
  use: {
    baseURL: "http://127.0.0.1:5174",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: [
    {
      command:
        process.platform === "win32"
          ? "..\\.venv\\Scripts\\python.exe ..\\scripts\\e2e_server.py"
          : "../.venv/bin/python ../scripts/e2e_server.py",
      url: "http://127.0.0.1:8011/api/v1/health",
      timeout: 60000,
    },
    {
      command: "npm run dev -- --port 5174 --strictPort",
      url: "http://127.0.0.1:5174",
      env: { VITE_PROXY_TARGET: "http://127.0.0.1:8011" },
      timeout: 60000,
    },
  ],
});
