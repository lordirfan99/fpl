import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/decision", timeout: 60000, reporter: "list", workers: 1,
  use: { baseURL: "http://localhost:4184", screenshot: "only-on-failure", trace: "retain-on-failure" },
  webServer: [
    { command: "node tests/fixtures/server.mjs", url: "http://127.0.0.1:4185", reuseExistingServer: true },
    { command: "npm run start -- --hostname localhost --port 4184", url: "http://localhost:4184/sign-in", reuseExistingServer: true, timeout: 120000,
      env: { AUTH_SECRET: "test-only-secret-not-for-production-123456789", AUTH_URL: "http://localhost:4184", AUTH_TRUST_HOST: "true", FPL_OWNER_EMAIL: "owner@example.com", FPL_DASHBOARD_READ_TOKEN: "test-read-only-".repeat(4), FPL_API_BASE_URL: "http://127.0.0.1:4185", FPL_DATA_BASE_URL: "http://127.0.0.1:4185/data" } },
  ],
});
