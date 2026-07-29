import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Overview, OverviewData } from "./overview";

const data: OverviewData = {
  attention: { total: 3, review: 1, failed: 1, retryable: 1, stale: 0, blocked: 0 },
  queue: { failed: 1 },
  agent_service: { state: "running", health: "offline", last_heartbeat: null },
  library: { publication_revision: "rev-1", library_digest: "d".repeat(64) },
  last_publication: null,
  autonomy: {
    rate: 0.96,
    numerator: 96,
    denominator: 100,
    exclusions: 2,
    target: 0.95,
    policy_revision: "1",
    history_status: "insufficient-history",
    telemetry_status: "complete",
    slo_state: "insufficient-history",
    coverage_gaps: []
  },
  budget: { spent_tokens: 100, reserved_tokens: 50, remaining: 850, limit: 1000 },
  recent_activity: []
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("overview metrics link to the exact filtered explanations", () => {
  render(<Overview project="demo" initialData={data} />);
  expect(screen.getByRole("link", { name: /1 Open reviews/ })).toHaveAttribute("href", "/reviews?status=open");
  expect(screen.getByRole("link", { name: /1 Failed/ })).toHaveAttribute("href", "/agent-service/runs?state=failed");
  expect(screen.getByText("96.0% observed")).toBeInTheDocument();
  expect(screen.getByText(/This window is not production SLO evidence/)).toBeInTheDocument();
  expect(screen.getByText(/850 of 1000 remaining/)).toBeInTheDocument();
  expect(screen.getByText(/offline/i)).toBeInTheDocument();
});

test("overview pauses while hidden and backs off after refresh failures", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
  vi.stubGlobal("fetch", fetchMock);
  Object.defineProperty(document, "hidden", { value: true, configurable: true });
  render(<Overview project="demo" initialData={data} />);

  await act(() => vi.advanceTimersByTimeAsync(15_000));
  expect(fetchMock).not.toHaveBeenCalled();

  Object.defineProperty(document, "hidden", { value: false, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Showing the last successful refresh/)).toBeVisible();

  await act(() => vi.advanceTimersByTimeAsync(29_999));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
