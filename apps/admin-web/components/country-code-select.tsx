"use client";

import { CaretDown, MagnifyingGlass } from "@phosphor-icons/react";
import {
  KeyboardEvent,
  type CSSProperties,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { PhoneCountry } from "@/lib/phone";

type CountryCodeSelectProps = {
  countries: readonly PhoneCountry[];
  value: string;
  onChange: (iso: string) => void;
  describedBy?: string;
};

const MAX_OPTIONS_HEIGHT = 276;
const POPOVER_GAP = 9;
const VIEWPORT_GUTTER = 16;

function CountryPopover({
  activeIndex,
  filteredCountries,
  listboxId,
  onActiveIndexChange,
  onChoose,
  onClose,
  onQueryChange,
  optionsHeight,
  optionsRef,
  popoverRef,
  query,
  searchRef,
  selectedIso,
  side,
}: {
  activeIndex: number;
  filteredCountries: PhoneCountry[];
  listboxId: string;
  onActiveIndexChange: (index: number) => void;
  onChoose: (country: PhoneCountry) => void;
  onClose: () => void;
  onQueryChange: (value: string) => void;
  optionsHeight: number;
  optionsRef: React.RefObject<HTMLDivElement>;
  popoverRef: React.RefObject<HTMLDivElement>;
  query: string;
  searchRef: React.RefObject<HTMLInputElement>;
  selectedIso: string;
  side: "above" | "below";
}) {
  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const length = filteredCountries.length;
      onActiveIndexChange(
        length ? (activeIndex + direction + length) % length : 0,
      );
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      onActiveIndexChange(
        event.key === "Home" ? 0 : filteredCountries.length - 1,
      );
      return;
    }
    if (event.key === "Enter" && filteredCountries[activeIndex]) {
      event.preventDefault();
      onChoose(filteredCountries[activeIndex]);
    }
  };

  return (
    <div
      ref={popoverRef}
      className={`country-code-popover ${side}`}
      style={
        {
          "--country-options-max-height": `${optionsHeight}px`,
        } as CSSProperties
      }
    >
      <div className="country-search-control">
        <MagnifyingGlass size={17} aria-hidden="true" />
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={handleKeyDown}
          aria-label="Search countries or calling codes"
          aria-controls={listboxId}
          aria-activedescendant={
            filteredCountries[activeIndex]
              ? `${listboxId}-${filteredCountries[activeIndex].iso}`
              : undefined
          }
          placeholder="Search country or code"
          autoComplete="off"
        />
      </div>
      <div
        ref={optionsRef}
        id={listboxId}
        className="country-code-options"
        role="listbox"
        aria-label="Country calling codes"
      >
        {!query && <span className="country-list-label">Countries</span>}
        {filteredCountries.map((country, index) => (
          <button
            key={country.iso}
            id={`${listboxId}-${country.iso}`}
            type="button"
            role="option"
            aria-selected={country.iso === selectedIso}
            data-country-iso={country.iso}
            className={`country-code-option ${index === activeIndex ? "active" : ""}`}
            onPointerMove={() => onActiveIndexChange(index)}
            onClick={() => onChoose(country)}
          >
            <span
              className={`fi fi-${country.iso.toLocaleLowerCase("en")}`}
              aria-hidden="true"
            />
            <span className="country-option-name">{country.name}</span>
            <span className="country-option-code">+{country.dialCode}</span>
          </button>
        ))}
        {filteredCountries.length === 0 && (
          <span className="country-empty-state">No countries found</span>
        )}
      </div>
    </div>
  );
}

export default function CountryCodeSelect({
  countries,
  value,
  onChange,
  describedBy,
}: CountryCodeSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [popoverLayout, setPopoverLayout] = useState<{
    side: "above" | "below";
    optionsHeight: number;
  }>({ side: "below", optionsHeight: MAX_OPTIONS_HEIGHT });
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const optionsRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();
  const selected =
    countries.find((country) => country.iso === value) ?? countries[0];

  const filteredCountries = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("en");
    const ordered = [...countries].toSorted(
      (a, b) => Number(b.priority) - Number(a.priority),
    );
    if (!normalizedQuery) return ordered;

    return ordered.filter((country) =>
      [country.name, country.iso, country.dialCode, `+${country.dialCode}`]
        .join(" ")
        .toLocaleLowerCase("en")
        .includes(normalizedQuery),
    );
  }, [countries, query]);

  useEffect(() => {
    if (!open) return;

    const selectedIndex = filteredCountries.findIndex(
      (country) => country.iso === value,
    );
    setActiveIndex(Math.max(0, selectedIndex));
    const frame = window.requestAnimationFrame(() =>
      searchRef.current?.focus({ preventScroll: true }),
    );

    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
    };
  }, [filteredCountries, open, value]);

  useEffect(() => {
    setActiveIndex((current) =>
      Math.min(current, Math.max(0, filteredCountries.length - 1)),
    );
  }, [filteredCountries.length]);

  useLayoutEffect(() => {
    if (!open) return;

    const updateLayout = () => {
      const trigger = triggerRef.current;
      const popover = popoverRef.current;
      const options = optionsRef.current;
      if (!trigger || !popover || !options) return;

      const triggerRect = trigger.getBoundingClientRect();
      if (triggerRect.width === 0 && triggerRect.height === 0) return;
      const viewport = window.visualViewport;
      const viewportTop = viewport?.offsetTop ?? 0;
      const viewportBottom =
        viewportTop + (viewport?.height ?? window.innerHeight);
      if (
        triggerRect.bottom <= viewportTop ||
        triggerRect.top >= viewportBottom
      ) {
        setOpen(false);
        setQuery("");
        return;
      }
      const spaceBelow = Math.max(
        0,
        viewportBottom - triggerRect.bottom - POPOVER_GAP - VIEWPORT_GUTTER,
      );
      const spaceAbove = Math.max(
        0,
        triggerRect.top - viewportTop - POPOVER_GAP - VIEWPORT_GUTTER,
      );
      const chromeHeight = Math.max(
        0,
        popover.getBoundingClientRect().height -
          options.getBoundingClientRect().height,
      );
      if (Math.max(spaceAbove, spaceBelow) < chromeHeight) {
        setOpen(false);
        setQuery("");
        window.requestAnimationFrame(() => triggerRef.current?.focus());
        return;
      }
      const desiredPopoverHeight =
        chromeHeight + Math.min(MAX_OPTIONS_HEIGHT, options.scrollHeight);
      const side =
        spaceBelow >= desiredPopoverHeight || spaceBelow >= spaceAbove
          ? "below"
          : "above";
      const availableHeight = side === "below" ? spaceBelow : spaceAbove;
      const optionsHeight = Math.max(
        0,
        Math.min(
          MAX_OPTIONS_HEIGHT,
          Math.floor(availableHeight - chromeHeight),
        ),
      );

      setPopoverLayout((current) =>
        current.side === side && current.optionsHeight === optionsHeight
          ? current
          : { side, optionsHeight },
      );
    };

    updateLayout();
    window.addEventListener("resize", updateLayout);
    window.addEventListener("scroll", updateLayout, true);
    window.visualViewport?.addEventListener("resize", updateLayout);
    window.visualViewport?.addEventListener("scroll", updateLayout);
    return () => {
      window.removeEventListener("resize", updateLayout);
      window.removeEventListener("scroll", updateLayout, true);
      window.visualViewport?.removeEventListener("resize", updateLayout);
      window.visualViewport?.removeEventListener("scroll", updateLayout);
    };
  }, [open]);

  const activeCountry = filteredCountries[activeIndex];

  useLayoutEffect(() => {
    if (!open || !activeCountry || !optionsRef.current) return;

    const options = optionsRef.current;
    const activeOption = options.querySelector<HTMLElement>(
      `[data-country-iso="${activeCountry.iso}"]`,
    );
    if (!activeOption) return;

    const optionsRect = options.getBoundingClientRect();
    const activeRect = activeOption.getBoundingClientRect();
    if (activeRect.top < optionsRect.top) {
      options.scrollTop -= optionsRect.top - activeRect.top;
    } else if (activeRect.bottom > optionsRect.bottom) {
      options.scrollTop += activeRect.bottom - optionsRect.bottom;
    }
  }, [activeCountry, open, popoverLayout.optionsHeight]);

  const close = (restoreTriggerFocus = false) => {
    setOpen(false);
    setQuery("");
    if (restoreTriggerFocus) {
      window.requestAnimationFrame(() => triggerRef.current?.focus());
    }
  };

  const choose = (country: PhoneCountry) => {
    onChange(country.iso);
    close(true);
  };

  if (!selected) return null;

  return (
    <div className="country-code-picker" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="country-code-trigger"
        aria-label={`Country code, ${selected.name} +${selected.dialCode}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-describedby={describedBy}
        onClick={() => {
          setOpen((current) => !current);
          setQuery("");
        }}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp"].includes(event.key)) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span
          className={`fi fi-${selected.iso.toLocaleLowerCase("en")}`}
          aria-hidden="true"
        />
        <span className="country-code-value">+{selected.dialCode}</span>
        <CaretDown size={14} weight="bold" aria-hidden="true" />
      </button>

      {open && (
        <CountryPopover
          activeIndex={activeIndex}
          filteredCountries={filteredCountries}
          listboxId={listboxId}
          onActiveIndexChange={setActiveIndex}
          onChoose={choose}
          onClose={() => close(true)}
          onQueryChange={(nextQuery) => {
            setQuery(nextQuery);
            setActiveIndex(0);
          }}
          optionsHeight={popoverLayout.optionsHeight}
          optionsRef={optionsRef}
          popoverRef={popoverRef}
          query={query}
          searchRef={searchRef}
          selectedIso={selected.iso}
          side={popoverLayout.side}
        />
      )}
    </div>
  );
}
