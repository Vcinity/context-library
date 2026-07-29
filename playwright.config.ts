import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: "https://127.0.0.1:8445",
    channel: "chrome",
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure"
  },
  webServer: {
    command: "bash scripts/run_e2e_server.sh",
    url: "https://127.0.0.1:8445/auth/login",
    ignoreHTTPSErrors: true,
    reuseExistingServer: false,
    timeout: 120_000
  }
});
