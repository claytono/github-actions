#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

run_prepare() {
  local output_file="$1"
  shift

  env -i \
    PATH="$PATH" \
    RUNNER_OS="Linux" \
    GITHUB_RUN_ID="123" \
    GITHUB_RUN_ATTEMPT="4" \
    GITHUB_JOB="build-flake-outputs" \
    GITHUB_WORKSPACE="$tmp/workspace" \
    GITHUB_OUTPUT="$output_file" \
    INPUT_HOSTNAME="" \
    INPUT_HOSTNAME_SUFFIX="" \
    INPUT_CONTINUE_FILE=".tailscale-ssh-continue" \
    INPUT_SSH_USER="runner" \
    "$@" \
    bash "$repo_root/tailscale-ssh/scripts/prepare.sh"
}

mkdir -p "$tmp/workspace"

out="$tmp/default.out"
run_prepare "$out" env
grep -qx "hostname=gha-123-4-build-flake-outputs" "$out"
grep -qx "continue-file=$tmp/workspace/.tailscale-ssh-continue" "$out"
grep -qx "ssh-user=runner" "$out"

out="$tmp/matrix.out"
run_prepare "$out" env INPUT_HOSTNAME_SUFFIX="Linux ARM64"
grep -qx "hostname=gha-123-4-build-flake-outputs-linux-arm64" "$out"

out="$tmp/custom.out"
run_prepare "$out" env INPUT_HOSTNAME="Dotfiles Flake" INPUT_HOSTNAME_SUFFIX="macOS_arm64" INPUT_CONTINUE_FILE="/tmp/continue"
grep -qx "hostname=dotfiles-flake-macos-arm64" "$out"
grep -qx "continue-file=/tmp/continue" "$out"

long_base="This hostname is intentionally far too long for a DNS label before the suffix is appended"
out="$tmp/long.out"
run_prepare "$out" env INPUT_HOSTNAME="$long_base" INPUT_HOSTNAME_SUFFIX="linux-arm64"
grep -qx "hostname=this-hostname-is-intentionally-far-too-long-for-a-d-linux-arm64" "$out"

long_suffix="This suffix is intentionally far too long for a DNS label when appended to the hostname"
out="$tmp/long-suffix.out"
run_prepare "$out" env INPUT_HOSTNAME="Dotfiles Flake" INPUT_HOSTNAME_SUFFIX="$long_suffix"
grep -qx "hostname=this-suffix-is-intentionally-far-too-long-for-a-dns-label-when" "$out"
