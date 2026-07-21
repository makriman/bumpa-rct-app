const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "summary",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function focusableElements(root: ParentNode | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(
    root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter(
    (element) =>
      !element.hasAttribute("inert") &&
      element.getAttribute("aria-hidden") !== "true",
  );
}

/** Keep a Tab key event inside a modal or mobile navigation boundary. */
export function trapTabKey(
  event: Pick<KeyboardEvent, "key" | "shiftKey" | "preventDefault">,
  root: ParentNode | null,
): boolean {
  if (event.key !== "Tab") return false;
  const focusable = focusableElements(root);
  const first = focusable[0];
  const last = focusable.at(-1);
  if (!first || !last) return false;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
    return true;
  }
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
    return true;
  }
  return false;
}
