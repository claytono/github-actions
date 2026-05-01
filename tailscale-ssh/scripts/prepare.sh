#!/usr/bin/env bash
set -euo pipefail

case "${RUNNER_OS:-}" in
  Linux | macOS) ;;
  *)
    echo "::error::tailscale-ssh supports Linux and macOS GitHub-hosted runners"
    exit 1
    ;;
esac

sanitize_hostname_part() {
  printf '%s' "$1" |
    tr '[:upper:]_' '[:lower:]-' |
    sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

truncate_hostname_part() {
  if (($2 <= 0)); then
    printf ''
    return 0
  fi

  printf '%s' "$1" | cut -c1-"$2" | sed -E 's/-+$//'
}

raw="${INPUT_HOSTNAME:-}"
if [[ -z "$raw" ]]; then
  raw="gha-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${GITHUB_JOB:-job}"
fi

hostname="$(truncate_hostname_part "$(sanitize_hostname_part "$raw")" 63)"
hostname_suffix="$(sanitize_hostname_part "${INPUT_HOSTNAME_SUFFIX:-}")"

if [[ -n "$hostname_suffix" ]]; then
  if ((${#hostname_suffix} >= 63)); then
    hostname="$(truncate_hostname_part "$hostname_suffix" 63)"
  else
    max_hostname_length=$((63 - ${#hostname_suffix} - 1))
    hostname="$(truncate_hostname_part "$hostname" "$max_hostname_length")"
    if [[ -n "$hostname" ]]; then
      hostname="$hostname-$hostname_suffix"
    else
      hostname="$hostname_suffix"
    fi
  fi
fi

if [[ -z "$hostname" || ! "$hostname" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  echo "::error::computed hostname is not a valid DNS label: $hostname"
  exit 1
fi

input_continue_file="${INPUT_CONTINUE_FILE:-.tailscale-ssh-continue}"
if [[ "$input_continue_file" = /* ]]; then
  continue_file="$input_continue_file"
else
  continue_file="${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}/$input_continue_file"
fi

ssh_user="${INPUT_SSH_USER:-}"
if [[ -z "$ssh_user" ]]; then
  ssh_user="$(id -un)"
fi

{
  echo "hostname=$hostname"
  echo "continue-file=$continue_file"
  echo "ssh-user=$ssh_user"
} >>"${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
