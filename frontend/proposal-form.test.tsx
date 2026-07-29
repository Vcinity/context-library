import axe from "axe-core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ProposalForm } from "./proposal-form";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("generic proposals default to create while decision proposals default to revise", () => {
  const generic = render(<ProposalForm project="demo" digest={"a".repeat(64)} />);
  expect(screen.getByLabelText("Operation")).toHaveValue("create");
  generic.unmount();
  render(<ProposalForm project="demo" digest={"a".repeat(64)} decisionId="ui-react" />);
  expect(screen.getByLabelText("Operation")).toHaveValue("revise");
});

test("proposal preview is keyboard operable, accessible, and locks duplicate submission", async () => {
  let mutationCalls = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/api/v1/session/csrf")) {
      return new Response(JSON.stringify({ status: "ok", errors: [], data: { csrf_token: "c".repeat(32) } }), { status: 200 });
    }
    if (url.endsWith("/preview")) {
      return new Response(JSON.stringify({ status: "ok", errors: [], data: {
        route: "agent",
        deterministic_checks: ["evidence-present"],
        estimated_input_tokens: 120,
        estimated_max_cost: 0.01,
        review_required: true,
        stale_source: false
      } }), { status: 200 });
    }
    if (url.endsWith("/library/proposals") && init?.method === "POST") {
      mutationCalls += 1;
      return await new Promise<Response>(() => undefined);
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  const { container } = render(<ProposalForm project="demo" digest={"a".repeat(64)} decisionId="ui-react" />);

  await user.type(screen.getByLabelText("Proposed decision"), "Use React with TypeScript.");
  await user.type(screen.getByLabelText("Rationale"), "Document the established stack.");
  await user.type(screen.getByLabelText("Evidence references, one per line"), "ticket://UI-1");
  await user.type(screen.getByLabelText("Authority/source metadata"), "Product Owner");
  screen.getByLabelText("Authority/source metadata").focus();
  await user.keyboard("{Enter}");

  const submit = await screen.findByRole("button", { name: "Submit proposal" });
  fireEvent.click(submit);
  fireEvent.click(submit);
  expect(await screen.findByRole("button", { name: "Submitting…" })).toBeDisabled();
  await waitFor(() => expect(mutationCalls).toBe(1));
  const mutationBody = fetchMock.mock.calls.find(
    ([url, init]) => String(url).endsWith("/library/proposals") && (init as RequestInit)?.method === "POST"
  )?.[1] as RequestInit;
  expect(JSON.parse(String(mutationBody.body)).idempotency_key).toBeTruthy();
  const result = await axe.run(container, {
    rules: { region: { enabled: false }, "color-contrast": { enabled: false } }
  });
  expect(result.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
});
