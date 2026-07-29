import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import { Feedback } from "./feedback";

export type OverviewData = {
  attention: { total: number; review: number; failed: number; retryable: number; stale: number; blocked: number };
  queue: Record<string, number>;
  agent_service: { state: string; health: string; last_heartbeat: string | null };
  library: { publication_revision: string; library_digest: string };
  last_publication: { status: string; created_at: string } | null;
  autonomy: {
    rate: number | null;
    numerator: number;
    denominator: number;
    exclusions: number;
    target: number;
    policy_revision: string;
    history_status: "complete" | "insufficient-history";
    telemetry_status: "complete" | "insufficient-telemetry";
    slo_state: "insufficient-telemetry" | "insufficient-history" | "no-data" | "met" | "missed";
    coverage_gaps: Array<{ producer: string | null; reason: string }>;
  };
  budget: { spent_tokens: number; reserved_tokens: number; remaining: number; limit: number };
  recent_activity: Array<{ id: string; event_type: string; created_at: string }>;
};

const links: Array<[keyof OverviewData["attention"], string, string]> = [
  ["review", "Open reviews", "/reviews?status=open"],
  ["failed", "Failed", "/agent-service/runs?state=failed"],
  ["retryable", "Retryable", "/agent-service/runs?state=retryable"],
  ["stale", "Stale", "/agent-service/runs?state=stale"],
  ["blocked", "Blocked", "/agent-service/runs?state=waiting-human"]
];

export function Overview({ project, initialData }: { project: string; initialData: OverviewData }) {
  const [data, setData] = useState(initialData);
  const [stale, setStale] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const timer = useRef<number>();
  const backoff = useRef(15_000);

  useEffect(() => {
    let stopped = false;
    async function refresh() {
      if (document.hidden) return schedule(15_000);
      try {
        const result = await request<OverviewData>(`/api/v1/projects/${encodeURIComponent(project)}/overview`);
        if (stopped) return;
        setData(result.data);
        setStale(false);
        setLastRefresh(new Date());
        backoff.current = 15_000;
      } catch {
        if (stopped) return;
        setStale(true);
        backoff.current = Math.min(backoff.current * 2, 120_000);
      }
      schedule(backoff.current);
    }
    function schedule(delay: number) {
      if (!stopped) timer.current = window.setTimeout(() => void refresh(), delay);
    }
    function visible() {
      if (!document.hidden) {
        window.clearTimeout(timer.current);
        void refresh();
      }
    }
    document.addEventListener("visibilitychange", visible);
    schedule(15_000);
    return () => {
      stopped = true;
      window.clearTimeout(timer.current);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [project]);

  return <>
    {stale && <Feedback state="stale">Showing the last successful refresh.</Feedback>}
    <p className="refresh-meta">Last refreshed <time dateTime={lastRefresh.toISOString()}>{lastRefresh.toLocaleTimeString()}</time></p>
    <section aria-labelledby="attention-heading">
      <h2 id="attention-heading">Attention · {data.attention.total}</h2>
      <div className="metric-grid">{links.map(([key, label, href]) =>
        <a className="metric-card" href={href} key={key}><strong>{data.attention[key]}</strong><span>{label}</span></a>
      )}</div>
    </section>
    <div className="dashboard-grid">
      <section className="panel"><h2>Lifecycle queue</h2>{Object.keys(data.queue).length
        ? <ul>{Object.entries(data.queue).map(([state, count]) => <li key={state}><a href={`/agent-service/runs?state=${encodeURIComponent(state)}`}>{state}: {count}</a></li>)}</ul>
        : <Feedback state="empty">No queued work.</Feedback>}</section>
      <section className="panel"><h2>Agent Service</h2><p><span className={`status status--${data.agent_service.health}`}>{data.agent_service.health}</span> Operator state: {data.agent_service.state}</p><a href="/agent-service">Inspect service</a></section>
      <section className="panel"><h2>Canonical library</h2><p>Revision {data.library.publication_revision}</p><p>{data.last_publication ? `Last publication: ${data.last_publication.status} at ${data.last_publication.created_at}` : "No publication recorded."}</p><a href="/publications">Publication history</a></section>
      <section className="panel">
        <h2>Autonomy · rolling 30 days</h2>
        <p>{data.autonomy.rate === null ? "No eligible data" : `${(data.autonomy.rate * 100).toFixed(1)}% observed`}</p>
        <p>{data.autonomy.numerator} autonomous / {data.autonomy.denominator} eligible · {data.autonomy.exclusions} excluded · target {Math.round(data.autonomy.target * 100)}%</p>
        <p>SLO state: {data.autonomy.slo_state} · history: {data.autonomy.history_status} · telemetry: {data.autonomy.telemetry_status}</p>
        {data.autonomy.slo_state.startsWith("insufficient-") && <p>This window is not production SLO evidence.</p>}
        <p>Policy revision {data.autonomy.policy_revision}</p>
        {data.autonomy.coverage_gaps.length > 0 && (
          <details>
            <summary>{data.autonomy.coverage_gaps.length} telemetry coverage gap(s)</summary>
            <ul>{data.autonomy.coverage_gaps.map((gap, index) => <li key={`${gap.producer}-${gap.reason}-${index}`}>{gap.producer ?? "all producers"}: {gap.reason}</li>)}</ul>
          </details>
        )}
      </section>
      <section className="panel"><h2>Token budget</h2><p>{data.budget.spent_tokens} spent · {data.budget.reserved_tokens} reserved</p><p>{data.budget.remaining} of {data.budget.limit} remaining</p><a href="/agent-service/runs">Run costs</a></section>
      <section className="panel"><h2>Recent activity</h2>{data.recent_activity.length
        ? <ol>{data.recent_activity.map((event) => <li key={event.id}><a href={`/audit?event_type=${encodeURIComponent(event.event_type)}`}>{event.event_type}</a> · {event.created_at}</li>)}</ol>
        : <Feedback state="empty">No recent activity.</Feedback>}</section>
    </div>
  </>;
}
