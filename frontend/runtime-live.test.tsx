import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { LibraryDetailRefresh, RuntimeHealth } from "./runtime-live";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

const health = {
  status: "healthy", database: "sqlite", heartbeats: [], active_leases: 0,
  retry_backlog: 0, notification_failures: 0, budgets: [], last_maintenance_actions: []
};

test("runtime health polls at fifteen seconds and reports stale data", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  Object.defineProperty(document, "hidden", { value: false, configurable: true });
  render(<RuntimeHealth project="demo" initial={health} />);
  await act(() => vi.advanceTimersByTimeAsync(15_000));
  expect(screen.getByText(/Health updates are stale/)).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(29_999));
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("library detail checks its digest when focus returns", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ status: "ok", data: { library_digest: "same" }, errors: [] })
  }));
  Object.defineProperty(document, "hidden", { value: false, configurable: true });
  render(<LibraryDetailRefresh project="demo" decisionId="one" digest="same" />);
  await act(async () => { window.dispatchEvent(new Event("focus")); await Promise.resolve(); });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(await screen.findByText(/Checked/)).toBeVisible();
});
