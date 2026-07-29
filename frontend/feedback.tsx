import { Component, ErrorInfo, ReactNode } from "react";

export function Feedback({ state, children }: { state: "loading" | "empty" | "stale" | "degraded" | "forbidden" | "offline"; children?: ReactNode }) {
  return <div className={`feedback feedback--${state}`} role={state === "loading" ? "status" : "alert"}>
    <strong>{state[0].toUpperCase() + state.slice(1)}</strong>{children && <span>{children}</span>}
  </div>;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(_error: Error, _info: ErrorInfo) { /* Safe UI only; server telemetry owns details. */ }
  render() {
    return this.state.failed
      ? <Feedback state="degraded">This section could not be displayed. Reload to retry.</Feedback>
      : this.props.children;
  }
}
