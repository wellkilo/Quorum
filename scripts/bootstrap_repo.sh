#!/usr/bin/env bash
set -euo pipefail

repo_url="${1:-https://github.com/wellkilo/Quorum.git}"

if [[ ! -f README.md || ! -f LICENSE ]]; then
  echo "Run this script from the Quorum repository root." >&2
  exit 1
fi

if [[ ! -d .git ]]; then
  git init -b main
fi

git branch -M main

if git remote get-url origin >/dev/null 2>&1; then
  current_origin="$(git remote get-url origin)"
  if [[ "${current_origin}" != "${repo_url}" ]]; then
    echo "Existing origin differs: ${current_origin}" >&2
    echo "Refusing to replace it automatically." >&2
    exit 1
  fi
else
  git remote add origin "${repo_url}"
fi

git config user.name "wellkilo"
git config user.email "wellkilo@foxmail.com"

echo "Repository ready. Review files, run tests, then commit explicitly."
