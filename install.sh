#!/usr/bin/env bash
# Links the skill into ~/.claude/skills so that /agy-plan is available in every project.
set -euo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills/agy-plan"
target="${HOME}/.claude/skills/agy-plan"

if [[ -e "$target" && ! -L "$target" ]]; then
  echo "error: $target exists and is not a symlink; move it away and retry" >&2
  exit 1
fi

mkdir -p "$(dirname "$target")"
ln -sfn "$source_dir" "$target"
echo "installed: $target -> $source_dir"

command -v agy >/dev/null || echo "warning: agy is not on PATH; /agy-plan needs the Antigravity CLI" >&2
