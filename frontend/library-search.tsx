import { FormEvent, useEffect, useState } from "react";
import { request } from "./api";
import { StatusBadge } from "./status";

type Decision = {
  decision_id: string;
  subject: string;
  decision: string;
  rationale: string;
  category: string;
  status: string;
  provenance: string;
  source_count: number;
};

export type SearchData = {
  items: Decision[];
  total: number;
  library_digest: string;
};

export function LibrarySearch({ project, initialData = null }: { project: string; initialData?: SearchData | null }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [results, setResults] = useState<SearchData | null>(initialData);
  const [message, setMessage] = useState(initialData ? `${initialData.total} decisions found` : "");

  async function runSearch() {
    setMessage("Searching…");
    try {
      const params = new URLSearchParams({ q: query });
      if (status) params.set("status", status);
      const result = await request<SearchData>(
        `/api/v1/projects/${encodeURIComponent(project)}/library/search?${params}`
      );
      setResults(result.data);
      setMessage(result.data.total ? `${result.data.total} decisions found` : "No decisions found");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Search failed");
    }
  }

  useEffect(() => {
    if (!initialData) void runSearch();
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    void runSearch();
  }

  return (
    <section aria-labelledby="search-heading">
      <h2 id="search-heading">Search decisions</h2>
      <form className="search-form" onSubmit={submit}>
        <label>
          Search
          <input value={query} maxLength={500} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <label>
          Status
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            {['authoritative', 'inferred', 'assumed', 'pending', 'superseded', 'excluded'].map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
        <button type="submit">Search</button>
      </form>
      <p role="status" aria-live="polite">{message}</p>
      {results && (
        <div className="decision-grid">
          {results.items.map((item) => (
            <article className="decision-card" key={item.decision_id}>
              <StatusBadge status={item.status} />
              <h3><a href={`/library/decisions/${encodeURIComponent(item.decision_id)}`}>{item.subject}</a></h3>
              <p>{item.decision}</p>
              <dl><dt>Category</dt><dd>{item.category}</dd><dt>Sources</dt><dd>{item.source_count}</dd></dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
