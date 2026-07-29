import axe from "axe-core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ReviewResolution } from "./review-resolution";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("review resolution requires rationale, locks duplicates, announces success, and restores focus", async () => {
  let mutations = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/v1/session/csrf")) {
      return new Response(JSON.stringify({ status: "ok", errors: [], data: { csrf_token: "c".repeat(32) } }), { status: 200 });
    }
    mutations += 1;
    return new Response(JSON.stringify({ status: "ok", errors: [], data: {
      review_id: "review-1", audit_event_id: "audit-1", work_id: "work-1", idempotent: false
    } }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  const { container } = render(<ReviewResolution project="demo" reviewId="review-1" choices={["retain-current", "adopt-candidate"]} />);

  await user.click(screen.getByLabelText("retain-current"));
  await user.type(screen.getByLabelText("Rationale"), "The current option has stronger evidence.");
  const submit = screen.getByRole("button", { name: "Resolve review" });
  fireEvent.click(submit);
  fireEvent.click(submit);

  const success = await screen.findByRole("heading", { name: "Review resolved" });
  await waitFor(() => expect(mutations).toBe(1));
  expect(success).toHaveFocus();
  expect(screen.getByRole("link", { name: "View audit event" })).toHaveAttribute("href", "/audit?event_id=audit-1");
  expect(screen.getByRole("link", { name: "View resulting work" })).toHaveAttribute("href", "/agent-service/runs?work_id=work-1");
  const result = await axe.run(container, { rules: { region: { enabled: false }, "color-contrast": { enabled: false } } });
  expect(result.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
});
