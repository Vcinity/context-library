import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import { Feedback } from "./feedback";

type Heartbeat = { process: string; instance_id: string; state: string; observed_at: string | null };
type Health = {
  status: string;
  database: string;
  heartbeats: Heartbeat[];
  active_leases: number;
  retry_backlog: number;
  notification_failures: number;
  budgets: Array<{ project: string; spent_tokens: number; reserved_tokens: number }>;
  last_maintenance_actions: Array<{ event_type: string; created_at: string }>;
};

export function RuntimeHealth({ project, initial }: { project: string; initial: Health }) {
  const [health, setHealth] = useState(initial);
  const [stale, setStale] = useState(false);
  const [refreshed, setRefreshed] = useState(new Date());
  const timer = useRef<number>();
  useEffect(() => {
    let stopped = false;
    let delay = 15_000;
    function schedule(wait: number) { if (!stopped) timer.current = window.setTimeout(() => void poll(), wait); }
    async function poll() {
      if (document.hidden) return schedule(15_000);
      try {
        const result = await request<Health>(`/api/v1/projects/${encodeURIComponent(project)}/health`);
        if (stopped) return;
        setHealth(result.data); setStale(false); setRefreshed(new Date()); delay = 15_000;
      } catch { if (stopped) return; setStale(true); delay = Math.min(delay * 2, 120_000); }
      schedule(delay);
    }
    function visible() { if (!document.hidden) { window.clearTimeout(timer.current); void poll(); } }
    document.addEventListener("visibilitychange", visible); schedule(delay);
    return () => { stopped = true; window.clearTimeout(timer.current); document.removeEventListener("visibilitychange", visible); };
  }, [project]);
  return <>
    <header className="page-header"><div><p className="eyebrow">Observed runtime</p><h1>Runtime health</h1></div><p><span className={`status status--${health.status}`}>{health.status}</span></p></header>
    {stale && <Feedback state="stale">Health updates are stale; showing the last successful refresh.</Feedback>}
    <p className="refresh-meta">Last refreshed <time dateTime={refreshed.toISOString()}>{refreshed.toLocaleTimeString()}</time></p>
    <div className="metric-grid"><article className="metric-card"><strong>{health.active_leases}</strong><span>Active leases</span></article><article className="metric-card"><strong>{health.retry_backlog}</strong><span>Retry backlog</span></article><article className="metric-card"><strong>{health.notification_failures}</strong><span>Notification failures</span></article></div>
    <section className="panel"><h2>Processes</h2><div className="review-list">{health.heartbeats.map((item) => <article key={`${item.process}-${item.instance_id}`}><h3>{item.process}</h3><p><span className={`status status--${item.state}`}>{item.state}</span> · {item.instance_id}</p><p>Last observed {item.observed_at ?? "never"}</p></article>)}</div></section>
    <div className="dashboard-grid"><section className="panel"><h2>Storage and budgets</h2><p>Database: {health.database}</p>{health.budgets.length ? <ul>{health.budgets.map((item) => <li key={item.project}>{item.project}: {item.spent_tokens} spent · {item.reserved_tokens} reserved</li>)}</ul> : <p>No budget usage recorded.</p>}</section><section className="panel"><h2>Last maintenance actions</h2>{health.last_maintenance_actions.length ? <ol>{health.last_maintenance_actions.map((item, index) => <li key={`${item.created_at}-${index}`}>{item.created_at} · {item.event_type}</li>)}</ol> : <p>No maintenance action recorded.</p>}</section></div>
  </>;
}

export function LibraryDetailRefresh({ project, decisionId, digest }: { project: string; decisionId: string; digest: string }) {
  const [message, setMessage] = useState("");
  useEffect(() => {
    let checking = false;
    async function refresh() {
      if (checking || document.hidden) return;
      checking = true;
      try {
        const result = await request<{ library_digest: string }>(`/api/v1/projects/${encodeURIComponent(project)}/library/decisions/${encodeURIComponent(decisionId)}`);
        if (result.data.library_digest !== digest) window.location.reload();
        else setMessage(`Checked ${new Date().toLocaleTimeString()}`);
      } catch { setMessage("Refresh unavailable; displayed decision may be stale."); }
      finally { checking = false; }
    }
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [project, decisionId, digest]);
  return <p className="refresh-meta" role="status">{message}</p>;
}
