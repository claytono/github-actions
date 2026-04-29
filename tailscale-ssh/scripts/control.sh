#!/usr/bin/env bash
set -euo pipefail

mode="${MODE:-blocking}"
timeout_minutes="${TIMEOUT_MINUTES:-30}"
continue_file="${CONTINUE_FILE:?CONTINUE_FILE is required}"
hostname="${TAILSCALE_HOSTNAME:?TAILSCALE_HOSTNAME is required}"
ssh_user="${SSH_USER:?SSH_USER is required}"
sleep_seconds="${CONTROL_SLEEP_SECONDS:-10}"

if [[ "$mode" != "blocking" && "$mode" != "background" ]]; then
  echo "::error::mode must be 'blocking' or 'background'"
  exit 2
fi

if [[ ! "$timeout_minutes" =~ ^[0-9]+$ ]] || ((timeout_minutes < 1 || timeout_minutes > 360)); then
  echo "::error::timeout-minutes must be an integer from 1 to 360"
  exit 2
fi

ipv4="$(tailscale ip -4 | head -n 1 || true)"
ipv6="$(tailscale ip -6 | head -n 1 || true)"
if [[ -z "$ipv4" && -z "$ipv6" ]]; then
  echo "::error::tailscale returned no IPv4 or IPv6 address"
  exit 1
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "ipv4=$ipv4" >>"$GITHUB_OUTPUT"
  echo "ipv6=$ipv6" >>"$GITHUB_OUTPUT"
fi

run_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-unknown/repo}/actions/runs/${GITHUB_RUN_ID:-unknown}"

print_info() {
  {
    echo "GitHub run: $run_url"
    echo "Tailscale hostname: $hostname"
    echo "SSH user: $ssh_user"
    echo "Connect by hostname: tailscale ssh $ssh_user@$hostname"
    if [[ -n "$ipv4" ]]; then
      echo "Tailscale IPv4: $ipv4"
      echo "Connect by IPv4: tailscale ssh $ssh_user@$ipv4"
    fi
    if [[ -n "$ipv6" ]]; then
      echo "Tailscale IPv6: $ipv6"
      echo "Connect by IPv6: tailscale ssh $ssh_user@$ipv6"
    fi
    echo "Continue file: $continue_file"
    echo "Continue command: touch '$continue_file'"
  } >&2
}

print_info

if [[ "$mode" == "background" ]]; then
  echo "Background mode: SSH remains available while later workflow steps keep the job running." >&2
  exit 0
fi

rm -f "$continue_file"
mkdir -p "$(dirname "$continue_file")"

deadline=$((SECONDS + timeout_minutes * 60))
next_reminder=$((SECONDS + 60))

while ((SECONDS < deadline)); do
  if [[ -e "$continue_file" ]]; then
    echo "Continue file found; resuming workflow." >&2
    exit 0
  fi

  if ((SECONDS >= next_reminder)); then
    print_info
    next_reminder=$((SECONDS + 60))
  fi

  sleep "$sleep_seconds"
done

echo "::error::Timed out waiting for continue file: $continue_file" >&2
exit 124
