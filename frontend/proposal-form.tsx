import { FormEvent, useRef, useState } from "react";
import { request } from "./api";

type Preview = {
  route: string;
  deterministic_checks: string[];
  estimated_input_tokens: number;
  estimated_max_cost: number;
  review_required: boolean;
  stale_source: boolean;
};

export function ProposalForm({ project, digest, decisionId = "" }: { project: string; digest: string; decisionId?: string }) {
  const [operation, setOperation] = useState(decisionId ? "revise" : "create");
  const [decision, setDecision] = useState("");
  const [rationale, setRationale] = useState("");
  const [evidence, setEvidence] = useState("");
  const [authority, setAuthority] = useState("");
  const [intent, setIntent] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const idempotencyKey = useRef(crypto.randomUUID());

  function body() {
    return {
      operation,
      decision_id: decisionId || null,
      proposed_fields: { decision },
      rationale,
      evidence_references: evidence.split("\n").map((item) => item.trim()).filter(Boolean),
      authority,
      publication_intent: intent,
      library_digest: digest
    };
  }

  async function previewProposal(event: FormEvent) {
    event.preventDefault();
    setMessage("Checking proposal…");
    try {
      const path = `/api/v1/projects/${encodeURIComponent(project)}/library/proposals/preview`;
      const result = await request<Preview>(path, { method: "POST", body: JSON.stringify(body()) });
      idempotencyKey.current = crypto.randomUUID();
      setPreview(result.data);
      setMessage("Preview ready");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Preview failed");
    }
  }

  async function submitProposal() {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setMessage("Submitting proposal…");
    try {
      const path = `/api/v1/projects/${encodeURIComponent(project)}/library/proposals`;
      const result = await request<{ proposal_id: string }>(path, {
        method: "POST",
        body: JSON.stringify({ ...body(), idempotency_key: idempotencyKey.current })
      });
      window.location.assign(`/library/proposals/${encodeURIComponent(result.data.proposal_id)}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Submission failed");
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <form className="proposal-form" onSubmit={previewProposal}>
      <label>Operation<select value={operation} onChange={(event) => setOperation(event.target.value)}>
        {['create', 'revise', 'supersede', 'exclude'].map((item) => <option key={item}>{item}</option>)}
      </select></label>
      <label>Proposed decision<textarea required value={decision} onChange={(event) => setDecision(event.target.value)} /></label>
      <label>Rationale<textarea required value={rationale} onChange={(event) => setRationale(event.target.value)} /></label>
      <label>Evidence references, one per line<textarea required value={evidence} onChange={(event) => setEvidence(event.target.value)} /></label>
      <label>Authority/source metadata<input required value={authority} onChange={(event) => setAuthority(event.target.value)} /></label>
      <label className="check"><input type="checkbox" checked={intent} onChange={(event) => setIntent(event.target.checked)} /> Request publication after checks</label>
      <button type="submit">Preview route</button>
      <p role="status" aria-live="polite">{message}</p>
      {preview && <section className="preview" aria-label="Proposal preview">
        <h2>Route: {preview.route}</h2>
        <p>{preview.estimated_input_tokens} estimated input tokens · ${preview.estimated_max_cost.toFixed(6)} maximum estimate</p>
        <p>{preview.review_required ? "Human review required" : "No policy-required review"}</p>
        <ul>{preview.deterministic_checks.map((check) => <li key={check}>{check}</li>)}</ul>
        <button type="button" disabled={preview.stale_source || submitting} onClick={submitProposal}>{submitting ? "Submitting…" : "Submit proposal"}</button>
      </section>}
    </form>
  );
}
