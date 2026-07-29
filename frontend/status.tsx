const descriptions: Record<string, string> = {
  authoritative: "Authoritative",
  inferred: "Inferred—not mandatory",
  assumed: "Assumed—not mandatory",
  pending: "Pending publication",
  superseded: "Superseded",
  excluded: "Excluded from projection"
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`status status--${status}`}>{descriptions[status] ?? status}</span>;
}
