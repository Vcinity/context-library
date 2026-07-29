import { FormEvent, useMemo, useState } from "react";
import { request } from "./api";

type Field = {
  value: unknown;
  source: string;
  editable: boolean;
  restart_required: boolean;
  constraints: { type: string; group: string; minimum?: number; maximum?: number };
};
type Model = { project: string; revision: number; fields: Record<string, Field>; deployment: Record<string, { configured: boolean }> };
type Revision = { revision: number; actor: string; reason: string; created_at: string; rolled_back_from: number | null };
type Impact = { valid: boolean; changed_fields: string[]; affected_queues: string[]; budget_effects: string[]; cache_invalidated: boolean; restart_required: boolean; errors: Array<{ field: string; message: string }> };

const key = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

export function Configuration({ project, initial, history, canAdmin }: { project: string; initial: Model; history: Revision[]; canAdmin: boolean }) {
  const [values, setValues] = useState<Record<string, unknown>>(() => Object.fromEntries(Object.entries(initial.fields).map(([name, field]) => [name, field.value])));
  const [reason, setReason] = useState("");
  const [impact, setImpact] = useState<Impact | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const groups = useMemo(() => ["Processing", "Budgets", "Notifications"], []);
  const changes = () => Object.fromEntries(Object.entries(values).filter(([name, value]) => JSON.stringify(value) !== JSON.stringify(initial.fields[name].value)));
  const payload = (why: string) => ({ schema_version: 1, expected_revision: initial.revision, changes: changes(), reason: why, idempotency_key: key() });

  async function preview(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("Validating impact…");
    try {
      const response = await request<Impact>(`/api/v1/projects/${encodeURIComponent(project)}/configuration/preview`, { method: "POST", body: JSON.stringify(payload(reason || "impact preview")) });
      setImpact(response.data); setMessage(response.data.valid ? "Preview is valid. Review the impact before applying." : "Correct the validation errors before applying.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Preview failed"); }
    finally { setBusy(false); }
  }
  async function apply() {
    if (!impact?.valid || !reason.trim()) { setMessage("Preview valid changes and provide a reason first."); return; }
    setBusy(true); setMessage("Applying configuration…");
    try {
      await request(`/api/v1/projects/${encodeURIComponent(project)}/configuration`, { method: "PUT", body: JSON.stringify(payload(reason)) });
      window.location.reload();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Apply failed"); setBusy(false); }
  }
  async function rollback(target: number) {
    if (!reason.trim()) { setMessage("Provide a reason before rollback."); return; }
    setBusy(true); setMessage(`Rolling back to revision ${target}…`);
    try {
      await request(`/api/v1/projects/${encodeURIComponent(project)}/configuration/rollback`, { method: "POST", body: JSON.stringify({ schema_version: 1, expected_revision: initial.revision, target_revision: target, reason, idempotency_key: key() }) });
      window.location.reload();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Rollback failed"); setBusy(false); }
  }
  function update(name: string, field: Field, raw: string | boolean) {
    let value: unknown = raw;
    if (field.constraints.type === "integer") value = Number.parseInt(String(raw), 10);
    if (field.constraints.type === "number") value = Number.parseFloat(String(raw));
    if (field.constraints.type === "string-list") value = String(raw).split(",").map((item) => item.trim()).filter(Boolean);
    setValues((current) => ({ ...current, [name]: value })); setImpact(null);
  }
  return <>
    <form className="configuration-form" onSubmit={preview}>
      {groups.map((group) => <fieldset key={group}><legend>{group}</legend><div className="configuration-grid">
        {Object.entries(initial.fields).filter(([, field]) => field.constraints.group === group).map(([name, field]) => <label key={name}>
          <span>{name.replaceAll("_", " ")} {field.restart_required && <small>(restart required)</small>}</span>
          {field.constraints.type === "boolean" ? <input type="checkbox" checked={Boolean(values[name])} disabled={!canAdmin || busy} onChange={(event) => update(name, field, event.target.checked)} /> : <input type={field.constraints.type === "string-list" ? "text" : "number"} step={field.constraints.type === "number" ? "any" : undefined} min={field.constraints.minimum} max={field.constraints.maximum} value={field.constraints.type === "string-list" ? (values[name] as string[]).join(", ") : String(values[name])} disabled={!canAdmin || busy} onChange={(event) => update(name, field, event.target.value)} />}
          <small className="meta">Source: {field.source}</small>
        </label>)}
      </div></fieldset>)}
      {canAdmin && <><label>Confirmation reason<textarea required maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label><div className="button-row"><button disabled={busy || Object.keys(changes()).length === 0}>Preview impact</button><button type="button" disabled={busy || !impact?.valid} onClick={() => void apply()}>Apply changes</button></div></>}
    </form>
    {impact && <section className="panel" aria-labelledby="impact-heading"><h2 id="impact-heading">Impact preview</h2>{impact.restart_required && <p className="feedback feedback--degraded">A worker restart is required.</p>}<p>Changed: {impact.changed_fields.join(", ") || "none"}</p><p>Queues: {impact.affected_queues.join(", ") || "none"} · Cache invalidated: {impact.cache_invalidated ? "yes" : "no"}</p>{impact.errors.map((error) => <p className="feedback feedback--forbidden" key={`${error.field}-${error.message}`}>{error.field}: {error.message}</p>)}</section>}
    <p role="status" aria-live="polite">{message}</p>
    <section className="panel"><h2>Deployment-owned configuration</h2><dl>{Object.entries(initial.deployment).map(([name, state]) => <><dt key={`${name}-term`}>{name.replaceAll("_", " ")}</dt><dd key={`${name}-value`}>{state.configured ? "configured" : "not configured"}</dd></>)}</dl><p className="meta">Secret values, paths, credentials, and commands are never exposed.</p></section>
    <section className="panel"><h2>Revision history</h2>{history.map((revision) => <article className="revision-row" key={revision.revision}><div><strong>Revision {revision.revision}</strong> · {revision.reason}<br /><small>{revision.actor} · {revision.created_at}{revision.rolled_back_from ? ` · rollback of ${revision.rolled_back_from}` : ""}</small></div>{canAdmin && revision.revision < initial.revision && <button type="button" disabled={busy} onClick={() => void rollback(revision.revision)}>Rollback to {revision.revision}</button>}</article>)}</section>
  </>;
}
