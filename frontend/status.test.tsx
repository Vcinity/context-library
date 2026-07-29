import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { StatusBadge } from "./status";

test("assumed status is explicit and never presented as mandatory", () => {
  render(<StatusBadge status="assumed" />);
  expect(screen.getByText("Assumed—not mandatory")).toBeInTheDocument();
});
