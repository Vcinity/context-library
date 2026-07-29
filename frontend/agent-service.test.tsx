import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AgentServiceLive } from "./agent-service";

const initial = {
  operator_state: "running",
  effective_state: "healthy",
  version: 1,
  last_heartbeat: "2026-07-17T00:00:00Z",
  active_work_id: null,
  project_tokens_used: 0,
  project_tokens_reserved: 0,
  project_token_budget: 1000
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("agent service polls every five seconds, pauses hidden, and backs off", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
  vi.stubGlobal("fetch", fetchMock);
  Object.defineProperty(document, "hidden", { value: true, configurable: true });
  render(<AgentServiceLive initial={initial} canAdmin={false} />);

  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(fetchMock).not.toHaveBeenCalled();

  Object.defineProperty(document, "hidden", { value: false, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Updates are stale/)).toBeVisible();
  expect(screen.getByText(/Last refreshed/)).toBeVisible();

  await act(() => vi.advanceTimersByTimeAsync(9999));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
