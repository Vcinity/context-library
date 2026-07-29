import { FormEvent, useEffect, useRef, useState } from "react";
import { request } from "./api";

type Result = { review_id: string; audit_event_id: string | null; work_id: string; idempotent: boolean };

export function ReviewResolution({ project, reviewId, choices }: { project: string; reviewId: string; choices: string[] }) {
  const [choice, setChoice] = useState("");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const key = useRef(crypto.randomUUID());
  const locked = useRef(false);
  const success = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (result) success.current?.focus();
  }, [result]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setMessage("Recording resolution…");
    try {
      const path = `/api/v1/projects/${encodeURIComponent(project)}/reviews/${encodeURIComponent(reviewId)}/resolve`;
      const response = await request<Result>(path, { method: "POST", body: JSON.stringify({ choice, rationale, idempotency_key: key.current }) });
      setResult(response.data);
      setMessage("Review resolved. Alternatives and rationale were preserved as evidence.");
    } catch (error) {
      locked.current = false;
      setBusy(false);
      setMessage(error instanceof Error ? error.message : "Resolution failed");
    }
  }

  if (result) return <section className="panel" aria-live="polite"><h2 ref={success} tabIndex={-1}>Review resolved</h2><p>{message}</p><p>{result.audit_event_id && <a href={`/audit?event_id=${encodeURIComponent(result.audit_event_id)}`}>View audit event</a>} · <a href={`/agent-service/runs?work_id=${encodeURIComponent(result.work_id)}`}>View resulting work</a></p></section>;

  return <form className="proposal-form" onSubmit={submit}>
    <fieldset><legend>Choose one resolution</legend>{choices.map((item) => <label className="choice" key={item}><input type="radio" name="choice" value={item} checked={choice === item} onChange={() => setChoice(item)} required />{item}</label>)}</fieldset>
    <label>Rationale<textarea value={rationale} onChange={(event) => setRationale(event.target.value)} required /></label>
    <button disabled={busy} type="submit">{busy ? "Resolving…" : "Resolve review"}</button>
    <p role="status" aria-live="polite">{message}</p>
  </form>;
}
