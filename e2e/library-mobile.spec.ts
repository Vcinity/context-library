import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ viewport: { width: 320, height: 720 } });

test("mobile keyboard library, detail, preview, and submission workflow", async ({ page }) => {
  const searchRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/library/search")) searchRequests.push(request.url());
  });
  await page.goto("/auth/login");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  await page.getByText("Menu · example", { exact: true }).click();
  await expect(page.locator(".mobile-nav").getByLabel("Project")).toBeVisible();
  await expect(page.getByRole("link", { name: /Open reviews/ })).toHaveAttribute("href", "/reviews?status=open");
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
  await page.goto("/library");

  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  expect(searchRequests).toEqual([]);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();

  const search = page.getByRole("textbox", { name: "Search" });
  await search.fill("React");
  await search.press("Enter");
  await expect.poll(() => searchRequests.length).toBe(1);
  const decision = page.getByRole("link", { name: /React remained the GUI framework choice/ });
  await expect(decision).toBeVisible();
  await decision.click();
  await expect(page.getByRole("heading", { name: "Evidence", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);

  await page.getByRole("link", { name: "Propose edit" }).click();
  await page.getByLabel("Proposed decision").fill("Continue using React and TypeScript.");
  await page.getByLabel("Rationale").fill("This follows the established product direction.");
  await page.getByLabel("Evidence references, one per line").fill("e2e-proposal-observation");
  await page.getByLabel("Authority/source metadata").fill("Product Owner");
  await page.getByLabel("Request publication after checks").check();
  await page.getByRole("button", { name: "Preview route" }).click();
  await expect(page.getByRole("region", { name: "Proposal preview" })).toBeVisible();
  await page.getByRole("button", { name: "Submit proposal" }).click();
  await expect(page).toHaveURL(/\/library\/proposals\/proposal_/);
  const proposalId = new URL(page.url()).pathname.split("/").at(-1)!;
  await expect(page.getByText(/canonical content has not changed/i)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);

  let reviewId = "";
  await expect.poll(async () => {
    const response = await page.request.get(`/api/v1/projects/example/library/proposals/${proposalId}`);
    const body = await response.json();
    reviewId = body.data.lifecycle.review_id ?? "";
    return reviewId;
  }).not.toBe("");
  await page.goto(`/reviews/${reviewId}`);
  await expect(page.getByRole("heading", { name: "Evidence", exact: true })).toBeVisible();
  await expect(page.locator("input[name='csrf_token']")).toHaveCount(0);
  await page.getByLabel("adopt-candidate").check();
  await page.getByLabel("Rationale").fill("Approve this exact proposal for canonical publication.");
  await page.getByRole("button", { name: "Resolve review" }).click();
  await expect(page.getByRole("heading", { name: "Review resolved" })).toBeFocused();

  let publicationId = "";
  await expect.poll(async () => {
    const response = await page.request.get(`/api/v1/projects/example/library/proposals/${proposalId}`);
    const body = await response.json();
    publicationId = body.data.lifecycle.publication_id ?? "";
    return {
      state: body.data.lifecycle.state,
      review: body.data.lifecycle.review_id,
      publication: publicationId
    };
  }).toEqual({ state: "succeeded", review: reviewId, publication: expect.stringMatching(/^publication_/) });

  await page.goto("/library");
  await page.getByRole("textbox", { name: "Search" }).fill("TypeScript browser proposal");
  await page.getByRole("textbox", { name: "Search" }).press("Enter");
  await expect(page.getByRole("link", { name: "TypeScript browser proposal" })).toBeVisible();
  await page.goto("/publications");
  await expect(page.getByText(proposalId, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: publicationId })).toBeVisible();
});
