export type UrlFilterDefinition = {
  defaultValue: string;
  allowedValues?: readonly string[];
};

export type UrlFilterDefinitions = Record<string, UrlFilterDefinition>;

export type UrlFilterValues<TDefinitions extends UrlFilterDefinitions> = {
  [TKey in keyof TDefinitions]: string;
};

function isAllowed(value: string, definition: UrlFilterDefinition) {
  return (
    value.length <= 200 &&
    (!definition.allowedValues || definition.allowedValues.includes(value))
  );
}

export function readUrlFilters<TDefinitions extends UrlFilterDefinitions>(
  search: string,
  definitions: TDefinitions,
): UrlFilterValues<TDefinitions> {
  const params = new URLSearchParams(search);
  return Object.fromEntries(
    Object.entries(definitions).map(([key, definition]) => {
      const candidate = params.get(key);
      return [
        key,
        candidate !== null && isAllowed(candidate, definition)
          ? candidate
          : definition.defaultValue,
      ];
    }),
  ) as UrlFilterValues<TDefinitions>;
}

export function writeUrlFilters<TDefinitions extends UrlFilterDefinitions>(
  href: string,
  definitions: TDefinitions,
  values: UrlFilterValues<TDefinitions>,
) {
  const url = new URL(href);
  for (const [key, definition] of Object.entries(definitions)) {
    const value = values[key];
    if (!value || value === definition.defaultValue) {
      url.searchParams.delete(key);
    } else if (isAllowed(value, definition)) {
      url.searchParams.set(key, value);
    }
  }
  return `${url.pathname}${url.search}${url.hash}`;
}
