import { useState } from "react";
import { request } from "./api";

type Project = { id: string; name: string };

export function ProjectSelector({ project, projects }: { project: string; projects: Project[] }) {
  const [selected, setSelected] = useState(project);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function change(next: string) {
    if (next === selected || busy) return;
    setBusy(true);
    setMessage("Switching project…");
    try {
      await request("/api/v1/session/project", {
        method: "POST",
        body: JSON.stringify({ project: next })
      });
      setSelected(next);
      window.location.reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Project switch failed");
      setBusy(false);
    }
  }

  return <div className="project-selector">
    <label>Project<select value={selected} disabled={busy} onChange={(event) => void change(event.target.value)}>
      {projects.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
    </select></label>
    <span className="sr-only" role="status" aria-live="polite">{message}</span>
  </div>;
}
