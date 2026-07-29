import { expect, test } from "@playwright/test";

test("administrator controls service, retries and cancels with audit evidence", async ({ page }) => {
  await page.goto("/auth/login");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.goto("/agent-service");

  await page.getByRole("button", { name: "Pause" }).click();
  await page.getByLabel("Reason").fill("Phase 6 browser pause test");
  await page.getByRole("button", { name: "Confirm pause" }).click();
  await expect(page.getByText(/Operator state paused/)).toBeVisible();

  await page.getByRole("button", { name: "Resume" }).click();
  await page.getByLabel("Reason").fill("Phase 6 browser resume test");
  await page.getByRole("button", { name: "Confirm resume" }).click();
  await expect(page.getByText(/Operator state running/)).toBeVisible();

  await page.getByRole("button", { name: "Drain" }).click();
  await page.getByLabel("Reason").fill("Phase 6 browser drain test");
  await page.getByRole("button", { name: "Confirm drain" }).click();
  await expect(page.getByText(/Operator state draining/)).toBeVisible();
  await page.getByRole("button", { name: "Resume" }).click();
  await page.getByLabel("Reason").fill("Resume after drain test");
  await page.getByRole("button", { name: "Confirm resume" }).click();
  await expect(page.getByText(/Operator state running/)).toBeVisible();

  await page.goto("/agent-service/runs?state=failed");
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Retry queued.")).toBeVisible();

  await page.goto("/agent-service/runs?state=running");
  await page
    .locator("article")
    .filter({ hasText: "e2e_cancellable_task" })
    .getByRole("link", { name: /Inspect processing run/ })
    .click();
  await page.getByRole("button", { name: "Cancel run" }).click();
  await page.getByLabel("Reason").fill("Phase 6 browser cancellation test");
  await page.getByRole("button", { name: "Request cancellation" }).click();
  await expect(page.getByText(/worker will stop at a safe boundary/)).toBeVisible();

  await page.goto("/audit?event_type=agent-run-cancel-requested");
  await expect(page.getByRole("heading", { name: "agent-run-cancel-requested" })).toBeVisible();
  await page.goto("/audit?event_type=agent-service-pause");
  await expect(page.getByRole("heading", { name: "agent-service-pause" })).toBeVisible();
});
