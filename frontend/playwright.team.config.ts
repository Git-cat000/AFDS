import { defineConfig } from "@playwright/test";
import config from "./playwright.config";

export default defineConfig({
  ...config,
  testMatch: ["team.spec.ts", "collaboration.spec.ts"],
  webServer: [
    ...(Array.isArray(config.webServer) ? config.webServer : [config.webServer!]),
    {
      command: "npm run dev -- --port 5175 --strictPort",
      url: "http://127.0.0.1:5175/",
      env: { VITE_PROXY_TARGET: "http://127.0.0.1:8012" },
      timeout: 60000,
    },
  ],
});
