import { FormEvent, useEffect, useRef, useState } from "react";
import { request } from "./api";

type Action = "pause" | "resume" | "drain";

type ServiceStatus = {
  operator_state: string;
  effective_state: string;
  version: number;
  last_heartbeat: string | null;
  active_work_id: string | null;
  project_tokens_used: number;
  project_tokens_reserved: number;
  project_token_budget: number;
};

function usePolling<T>(path: string, initial: T) {
  const [data, setData] = useState(initial);
  const [stale, setStale] = useState(false);
  const [refreshed, setRefreshed] = useState(new Date());
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    let delay = 5000;
    async function poll() {
      if (document.hidden) return schedule(5000);
      try {
        const response = await request<T>(path);
        if (stopped) return;
        setData(response.data);
        setStale(false);
        setRefreshed(new Date());
        delay = 5000;
      } catch {
        if (stopped) return;
        setStale(true);
        delay = Math.min(delay * 2, 60000);
      }
      schedule(delay);
    }
    function schedule(wait: number) {
      if (!stopped) timer = window.setTimeout(() => void poll(), wait);
    }
    function visible() {
      if (!document.hidden) {
        window.clearTimeout(timer);
        void poll();
      }
    }
    document.addEventListener("visibilitychange", visible);
    schedule(5000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [path]);
  return { data, stale, refreshed };
}

export function AgentServiceLive({ initial, canAdmin }: { initial: ServiceStatus; canAdmin: boolean }) {
  const { data, stale, refreshed } = usePolling("/api/v1/agent-service", initial);
  return <section className="panel">
    <h2>State</h2>
    <p><span className={`status status--${data.effective_state}`}>{data.effective_state}</span></p>
    <p>Operator state {data.operator_state} · version {data.version}</p>
    <p>Last heartbeat {data.last_heartbeat ?? "not observed"}</p>
    <p>Active work {data.active_work_id ?? "none"}</p>
    <p>{data.project_tokens_used} spent · {data.project_tokens_reserved} reserved of {data.project_token_budget}</p>
    {stale && <p className="feedback feedback--stale" role="status">Updates are stale; retrying.</p>}
    <p>Last refreshed <time dateTime={refreshed.toISOString()}>{refreshed.toLocaleTimeString()}</time></p>
    {canAdmin && <AgentServiceControls state={data.operator_state} version={data.version} />}
  </section>;
}

type RunLiveData = { status: string; input_tokens: number; output_tokens: number; finished_at: string | null };

export function AgentRunLive({ project, runId, initial }: { project: string; runId: string; initial: RunLiveData }) {
  const path = `/api/v1/projects/${encodeURIComponent(project)}/agent-runs/${encodeURIComponent(runId)}`;
  const { data, stale, refreshed } = usePolling<RunLiveData>(path, initial);
  return <section className="panel" aria-labelledby="live-run-heading">
    <h2 id="live-run-heading">Live run state</h2>
    <p><span className={`status status--${data.status}`}>{data.status}</span></p>
    <p>{data.input_tokens} input · {data.output_tokens} output</p>
    <p>{data.finished_at ? `Finished ${data.finished_at}` : "Run is active"}</p>
    {stale && <p className="feedback feedback--stale" role="status">Updates are stale; retrying.</p>}
    <p>Last refreshed <time dateTime={refreshed.toISOString()}>{refreshed.toLocaleTimeString()}</time></p>
  </section>;
}

export function AgentServiceControls({ state, version }: { state: string; version: number }) {
  const [action, setAction] = useState<Action | null>(null);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const actions: Action[] = state === "running" ? ["pause", "drain"] : ["resume"];

  function open(selected: Action) {
    setAction(selected);
    setReason("");
    setMessage("");
    dialog.current?.showModal();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!action || busy) return;
    setBusy(true);
    setMessage(`${action} in progress…`);
    try {
      await request(`/api/v1/agent-service/${action}`, {
        method: "POST",
        body: JSON.stringify({
          schema_version: 1,
          expected_version: version,
          reason,
          idempotency_key: crypto.randomUUID()
        })
      });
      window.location.reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Control failed");
      setBusy(false);
    }
  }

  return <div className="service-controls">
    <p>Operational controls</p>
    {actions.map((item) => <button type="button" key={item} onClick={() => open(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}
    <dialog ref={dialog} aria-labelledby="control-heading">
      <form method="dialog"><button className="dialog-close" aria-label="Close">×</button></form>
      <form onSubmit={submit}>
        <h2 id="control-heading">Confirm {action}</h2>
        <label>Reason<textarea required maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <button disabled={busy} type="submit">Confirm {action}</button>
        <p role="status" aria-live="polite">{message}</p>
      </form>
    </dialog>
  </div>;
}

export function RunCancel({ project, runId, status }: { project: string; runId: string; status: string }) {
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  if (status === "cancel-requested") return <p role="status">Cancellation requested; waiting for worker acknowledgement.</p>;
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setMessage("Requesting cancellation…");
    try {
      await request(`/api/v1/projects/${encodeURIComponent(project)}/agent-runs/${encodeURIComponent(runId)}/cancel`, {
        method: "POST",
        body: JSON.stringify({ schema_version: 1, reason, idempotency_key: crypto.randomUUID() })
      });
      setMessage("Cancellation requested; the worker will stop at a safe boundary.");
      dialog.current?.close();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Cancellation failed");
      setBusy(false);
    }
  }
  return <div><button type="button" onClick={() => dialog.current?.showModal()}>Cancel run</button><dialog ref={dialog} aria-labelledby="cancel-heading"><form method="dialog"><button className="dialog-close" aria-label="Close">×</button></form><form onSubmit={submit}><h2 id="cancel-heading">Confirm cancellation</h2><label>Reason<textarea required maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label><button disabled={busy}>Request cancellation</button></form></dialog><p role="status" aria-live="polite">{message}</p></div>;
}

export function RunRetry({ project, workId }: { project: string; workId: string }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function retry() {
    if (busy) return;
    setBusy(true);
    setMessage("Queueing retry…");
    try {
      await request(`/api/v1/projects/${encodeURIComponent(project)}/runs/${encodeURIComponent(workId)}/retry`, {
        method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }
      });
      setMessage("Retry queued.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Retry failed");
      setBusy(false);
    }
  }
  return <div><button type="button" disabled={busy} onClick={() => void retry()}>Retry</button><p role="status" aria-live="polite">{message}</p></div>;
}
