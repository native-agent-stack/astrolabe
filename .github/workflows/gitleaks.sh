#!/usr/bin/env bash
set -euo pipefail
git fetch origin main
if [ "$GITHUB_EVENT_NAME" = pull_request ]; then SCAN_BEFORE="$(git merge-base origin/main "$SCAN_HEAD")"; fi
if [ "$SCAN_BEFORE" = 0000000000000000000000000000000000000000 ]; then range="-1 $SCAN_HEAD"; else range="$SCAN_BEFORE..$SCAN_HEAD"; fi
export GITLEAKS_CONFIG_TOML=$'[extend]\nuseDefault = true\n'
"$RUNNER_TEMP/gitleaks" git --redact --no-banner --log-opts="$range" .
config="$RUNNER_TEMP/main.gitleaks.toml"
if [ -n "$(git ls-tree --name-only origin/main -- .gitleaks.toml)" ]; then git show origin/main:.gitleaks.toml > "$config"; else cp .gitleaks.toml "$config"; fi
ignore="$RUNNER_TEMP/main.gitleaksignore"
if [ -n "$(git ls-tree --name-only origin/main -- .gitleaksignore)" ]; then git show origin/main:.gitleaksignore > "$ignore"; else : > "$ignore"; fi
"$RUNNER_TEMP/gitleaks" git --config="$config" --gitleaks-ignore-path="$ignore" --ignore-gitleaks-allow --redact --no-banner --log-opts="$range" "$(git rev-parse --absolute-git-dir)"
