import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("operator traces runtime, publication failure, and filtered audit", async ({ page }) => {
  await page.goto("/auth/login");
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.goto("/health");
  await expect(page.getByRole("heading", { name: "Runtime health" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Processes" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "api", exact: true })).toBeVisible();

  await page.goto("/publications");
  await expect(page.getByText("Publication failed", { exact: true })).toBeVisible();
  await expect(page.getByText(/last known-good content remains canonical/)).toBeVisible();
  await expect(page.getByText("e2e-secret")).toHaveCount(0);

  await page.goto("/audit?action=runtime-e2e-observed&actor=fixture%3Aoperator");
  await expect(page.getByRole("heading", { name: "runtime-e2e-observed" })).toBeVisible();
  await expect(page.getByText("runtime:old → runtime:new")).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
});
