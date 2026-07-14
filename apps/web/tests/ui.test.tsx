import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Badge, Brand, Metric, Modal, StatePanel } from "@/components/ui";

describe("shared UI", () => {
  it("uses a distinct, accessible Bumpa Bestie lockup", () => {
    const { container } = render(<Brand />);
    const home = screen.getByRole("link", { name: "Bumpa Bestie home" });
    expect(home).toHaveAttribute("href", "/");
    expect(screen.getByText("Bumpa")).toHaveClass("brand-name");
    expect(screen.getByText("Bestie")).toHaveClass("brand-bestie");
    expect(container.querySelector(".brand-mark svg")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  });

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

  it("traps keyboard focus inside dialogs and restores the opener", async () => {
    const onClose = vi.fn();
    function Harness() {
      const [open, setOpen] = React.useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Open report</button>
          {open && (
            <Modal
              title="Export report"
              onClose={() => {
                onClose();
                setOpen(false);
              }}
              actions={<button>Confirm export</button>}
            >
              <input aria-label="Export name" />
            </Modal>
          )}
        </>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open report" });
    opener.focus();
    fireEvent.click(opener);
    // The close button is the first focusable element in the dialog.
    const close = screen.getByRole("button", { name: "Close dialog" });
    expect(close).toHaveFocus();
    const confirm = screen.getByRole("button", { name: "Confirm export" });
    confirm.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(opener).toHaveFocus());
  });
});
