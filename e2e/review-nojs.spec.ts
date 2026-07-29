import { expect, test } from "@playwright/test";

test.use({ javaScriptEnabled: false, viewport: { width: 320, height: 720 } });

test("no-JavaScript review resolution preserves audit and work links", async ({ page }) => {
  await page.goto("/auth/login");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.goto("/reviews?status=open");
  await page.getByRole("link", { name: "Resolve no-JavaScript evidence conflict" }).click();
  await expect(page.locator("[data-island='review-resolution'] input[name='csrf_token']")).toHaveCount(1);
  await page.getByLabel("retain-current").check();
  await page.getByLabel("Rationale").fill("The no-JavaScript path preserves this evidence.");
  await page.getByRole("button", { name: "Resolve review" }).click();
  await expect(page.getByRole("heading", { name: "Review resolved" })).toBeVisible();
  await expect(page.getByRole("link", { name: "View audit event" })).toBeVisible();
  await expect(page.getByRole("link", { name: "View resulting work" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  await page.getByRole("link", { name: "View audit event" }).click();
  await expect(page.getByRole("heading", { name: "review-resolved" })).toBeVisible();
});
