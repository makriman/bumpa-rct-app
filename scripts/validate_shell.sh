#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

shell_files=()
while IFS= read -r file; do
  shell_files+=("$file")
done < <(find scripts infra -type f -name '*.sh' -print | sort)

if ((${#shell_files[@]} == 0)); then
  echo "No shell files found" >&2
  exit 2
fi

for file in "${shell_files[@]}"; do
  bash -n "$file"
done

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x "${shell_files[@]}"
else
  echo "shellcheck is not installed; bash syntax validation completed only" >&2
fi
