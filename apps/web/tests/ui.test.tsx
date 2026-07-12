import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Badge, Metric, StatePanel } from "@/components/ui";

describe("shared UI", () => {
  it("renders semantic metric content", () => {
    render(
      <Metric label="Active SMEs" value="24" trend="+3" note="Last 30 days" />,
    );
    expect(screen.getByText("Active SMEs")).toBeInTheDocument();
    expect(screen.getByText("24")).toBeInTheDocument();
    expect(screen.getByText("+3")).toHaveClass("trend-up");
  });

  it("maps status badges to an accessible text label", () => {
    render(<Badge>Connected</Badge>);
    expect(screen.getByText("Connected")).toHaveClass("badge-success");
  });

  it("announces loading state", () => {
    const { container } = render(<StatePanel type="loading" />);
    expect(container.querySelector("[aria-busy='true']")).toBeInTheDocument();
    expect(screen.getByText("Loading content")).toBeInTheDocument();
  });

  it("offers recovery for errors", () => {
    render(<StatePanel type="error" action={<button>Try again</button>} />);
    expect(
      screen.getByRole("heading", { name: "Something went wrong" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });
});
