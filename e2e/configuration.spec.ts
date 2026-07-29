import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("administrator previews, applies, and rolls back configuration", async ({ page }) => {
  await page.goto("/auth/login");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.goto("/configuration");

  await expect(page.getByRole("heading", { name: "Configuration", exact: true })).toBeVisible();
  await page.getByLabel(/semantic threshold/i).fill("0.65");
  await page.getByLabel("Confirmation reason").fill("E2E validated threshold");
  await page.getByRole("button", { name: "Preview impact" }).click();
  await expect(page.getByRole("heading", { name: "Impact preview" })).toBeVisible();
  await expect(page.getByText(/Preview is valid/)).toBeVisible();
  await page.getByRole("button", { name: "Apply changes" }).click();

  await expect(page.getByText(/revision 2/i).first()).toBeVisible();
  await expect(page.getByLabel(/semantic threshold/i)).toHaveValue("0.65");
  await page.getByLabel("Confirmation reason").fill("E2E restore known good");
  await page.getByRole("button", { name: "Rollback to 1" }).click();
  await expect(page.getByText(/revision 3/i).first()).toBeVisible();
  await expect(page.getByLabel(/semantic threshold/i)).toHaveValue("0.5");

  await page.setViewportSize({ width: 320, height: 760 });
  await expect(page.getByRole("heading", { name: "Revision history" })).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
});
