import { defineConfig } from "@playwright/test";
import config from "./playwright.config";

export default defineConfig({
  ...config,
  testMatch: "team-deployment.spec.ts",
  timeout: 240000,
});
