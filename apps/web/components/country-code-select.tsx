"use client";

import { CaretDown, MagnifyingGlass } from "@phosphor-icons/react";
import {
  KeyboardEvent,
  useEffect,
  useId,
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

export default function CountryCodeSelect({
  countries,
  value,
  onChange,
  describedBy,
}: CountryCodeSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
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
      searchRef.current?.focus(),
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

  const close = () => {
    setOpen(false);
    setQuery("");
  };

  const choose = (country: PhoneCountry) => {
    onChange(country.iso);
    close();
  };

  const handleListKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      rootRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => {
        const length = filteredCountries.length;
        return length ? (current + direction + length) % length : 0;
      });
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setActiveIndex(event.key === "Home" ? 0 : filteredCountries.length - 1);
      return;
    }
    if (event.key === "Enter" && filteredCountries[activeIndex]) {
      event.preventDefault();
      choose(filteredCountries[activeIndex]);
    }
  };

  if (!selected) return null;

  return (
    <div className="country-code-picker" ref={rootRef}>
      <button
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
        <div className="country-code-popover">
          <div className="country-search-control">
            <MagnifyingGlass size={17} aria-hidden="true" />
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={handleListKeyDown}
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
                aria-selected={country.iso === selected.iso}
                data-country-iso={country.iso}
                className={`country-code-option ${
                  index === activeIndex ? "active" : ""
                }`}
                onPointerMove={() => setActiveIndex(index)}
                onClick={() => choose(country)}
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
      )}
    </div>
  );
}
