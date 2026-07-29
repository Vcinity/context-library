import { createRoot } from "react-dom/client";
import { LibrarySearch, SearchData } from "./library-search";
import { ProposalForm } from "./proposal-form";
import { ErrorBoundary } from "./feedback";
import { Overview, OverviewData } from "./overview";
import { ProjectSelector } from "./project-selector";
import { ReviewResolution } from "./review-resolution";
import { AgentRunLive, AgentServiceLive, RunCancel, RunRetry } from "./agent-service";
import { Configuration } from "./configuration";
import { LibraryDetailRefresh, RuntimeHealth } from "./runtime-live";
import "./styles.css";

document.querySelectorAll<HTMLElement>("[data-island='library-search']").forEach((node) => {
  const initialData = node.dataset.initialSearch
    ? JSON.parse(node.dataset.initialSearch) as SearchData
    : null;
  createRoot(node).render(<ErrorBoundary><LibrarySearch project={node.dataset.project ?? ""} initialData={initialData} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='proposal-form']").forEach((node) => {
  createRoot(node).render(
    <ErrorBoundary><ProposalForm
      project={node.dataset.project ?? ""}
      digest={node.dataset.digest ?? ""}
      decisionId={node.dataset.decisionId}
    /></ErrorBoundary>
  );
});

document.querySelectorAll<HTMLElement>("[data-island='project-selector']").forEach((node) => {
  const projects = JSON.parse(node.dataset.projects ?? "[]") as Array<{ id: string; name: string }>;
  createRoot(node).render(<ErrorBoundary><ProjectSelector project={node.dataset.project ?? ""} projects={projects} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='overview']").forEach((node) => {
  const data = JSON.parse(node.dataset.initialOverview ?? "{}") as OverviewData;
  createRoot(node).render(<ErrorBoundary><Overview project={node.dataset.project ?? ""} initialData={data} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='review-resolution']").forEach((node) => {
  const choices = JSON.parse(node.dataset.choices ?? "[]") as string[];
  createRoot(node).render(<ErrorBoundary><ReviewResolution project={node.dataset.project ?? ""} reviewId={node.dataset.reviewId ?? ""} choices={choices} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='agent-service-controls']").forEach((node) => {
  const initial = JSON.parse(node.dataset.initial ?? "{}") as Parameters<typeof AgentServiceLive>[0]["initial"];
  createRoot(node).render(<ErrorBoundary><AgentServiceLive initial={initial} canAdmin={node.dataset.canAdmin === "true"} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='agent-run-live']").forEach((node) => {
  const initial = JSON.parse(node.dataset.initial ?? "{}") as Parameters<typeof AgentRunLive>[0]["initial"];
  createRoot(node).render(<ErrorBoundary><AgentRunLive project={node.dataset.project ?? ""} runId={node.dataset.runId ?? ""} initial={initial} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='run-cancel']").forEach((node) => {
  createRoot(node).render(<ErrorBoundary><RunCancel project={node.dataset.project ?? ""} runId={node.dataset.runId ?? ""} status={node.dataset.status ?? ""} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='run-retry']").forEach((node) => {
  createRoot(node).render(<ErrorBoundary><RunRetry project={node.dataset.project ?? ""} workId={node.dataset.workId ?? ""} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='configuration']").forEach((node) => {
  const initial = JSON.parse(node.dataset.initial ?? "{}") as Parameters<typeof Configuration>[0]["initial"];
  const history = JSON.parse(node.dataset.history ?? "[]") as Parameters<typeof Configuration>[0]["history"];
  createRoot(node).render(<ErrorBoundary><Configuration project={node.dataset.project ?? ""} initial={initial} history={history} canAdmin={node.dataset.canAdmin === "true"} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='runtime-health']").forEach((node) => {
  const initial = JSON.parse(node.dataset.initial ?? "{}") as Parameters<typeof RuntimeHealth>[0]["initial"];
  createRoot(node).render(<ErrorBoundary><RuntimeHealth project={node.dataset.project ?? ""} initial={initial} /></ErrorBoundary>);
});

document.querySelectorAll<HTMLElement>("[data-island='library-detail-refresh']").forEach((node) => {
  createRoot(node).render(<ErrorBoundary><LibraryDetailRefresh project={node.dataset.project ?? ""} decisionId={node.dataset.decisionId ?? ""} digest={node.dataset.digest ?? ""} /></ErrorBoundary>);
});
