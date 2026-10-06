#!/usr/bin/env bash
# The one way to deploy. Policy lives in railway_deploy.py (pinned project and
# service, the 14:30-15:30 ET no-deploy window); this wrapper only EXECUTES the
# command that script prints, in the shell where the `railway` npm shim resolves.
#
#     scripts/deploy.sh backend
#     scripts/deploy.sh bot
#     scripts/deploy.sh backend --force
#
# Never run two of these at once, and never in parallel with anything that
# changes directory — see the deploy rules in memory (railway-two-services).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

line="$(python "$here/railway_deploy.py" "$@" --print-cmd)" || exit $?
# Everything the policy script prints before the CMD line is the target
# summary — show it, then act only on the CMD line.
printf '%s\n' "$line" | grep -v '^CMD'
cmd_line="$(printf '%s\n' "$line" | grep '^CMD' | tail -1)"
[ -n "$cmd_line" ] || { echo "no command produced (refused?)"; exit 3; }

IFS=$'\t' read -r _tag cwd rest <<<"$cmd_line"
# shellcheck disable=SC2206
args=($rest)
echo "RUN      railway ${args[*]}   (cwd $cwd)"
( cd "$cwd" && railway "${args[@]}" ) 2>&1 | grep -vE 'agent tooling|Ask the user|more effectively|^$'
