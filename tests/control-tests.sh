#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/bin"
cat >"$tmp/bin/tailscale" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "ip" && "$2" == "-4" ]]; then
  echo "100.64.0.10"
  exit 0
fi
if [[ "$1" == "ip" && "$2" == "-6" ]]; then
  echo "fd7a:115c:a1e0::1"
  exit 0
fi
echo "unexpected tailscale args: $*" >&2
exit 1
STUB
chmod +x "$tmp/bin/tailscale"

export PATH="$tmp/bin:$PATH"
export GITHUB_SERVER_URL="https://github.com"
export GITHUB_REPOSITORY="claytono/github-actions"
export GITHUB_RUN_ID="123"
export TAILSCALE_HOSTNAME="gha-123-1-test"
export SSH_USER="runner"
export CONTINUE_FILE="$tmp/continue"
export TIMEOUT_MINUTES="1"
export CONTROL_SLEEP_SECONDS="1"

MODE="invalid" bash "$repo_root/tailscale-ssh/scripts/control.sh" >/tmp/control-invalid.out 2>&1 && {
  echo "invalid mode unexpectedly passed" >&2
  exit 1
}

MODE="background" bash "$repo_root/tailscale-ssh/scripts/control.sh" >/tmp/control-background.out 2>&1
grep -q "tailscale ssh runner@gha-123-1-test" /tmp/control-background.out
grep -q "tailscale ssh runner@100.64.0.10" /tmp/control-background.out
grep -q "tailscale ssh runner@fd7a:115c:a1e0::1" /tmp/control-background.out

MODE="blocking" bash "$repo_root/tailscale-ssh/scripts/control.sh" >/tmp/control-blocking.out 2>&1 &
pid=$!
sleep 1
touch "$CONTINUE_FILE"
wait "$pid"
grep -q "Continue file found" /tmp/control-blocking.out
