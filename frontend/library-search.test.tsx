import axe from "axe-core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { LibrarySearch } from "./library-search";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("search is keyboard operable, announces results, and has no serious axe violations", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const matched = url.includes("q=React");
    return new Response(JSON.stringify({
      schema_version: 1,
      status: "ok",
      errors: [],
      data: {
        total: matched ? 1 : 0,
        library_digest: "a".repeat(64),
        items: matched ? [{
          decision_id: "ui-react",
          subject: "Use React",
          decision: "Use React for the UI.",
          rationale: "Established stack.",
          category: "UI",
          status: "authoritative",
          provenance: "explicit",
          source_count: 1
        }] : []
      }
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  const { container } = render(<LibrarySearch project="demo" />);
  await screen.findByText("No decisions found");

  const input = screen.getByLabelText("Search");
  input.focus();
  await user.keyboard("React{Enter}");

  expect(await screen.findByRole("link", { name: "Use React" })).toHaveAttribute(
    "href",
    "/library/decisions/ui-react"
  );
  expect(screen.getByRole("status")).toHaveTextContent("1 decisions found");
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("q=React"), expect.anything());
  const result = await axe.run(container, {
    rules: { region: { enabled: false }, "color-contrast": { enabled: false } }
  });
  expect(result.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
});
