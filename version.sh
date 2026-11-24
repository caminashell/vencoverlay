#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
usage() {
    cat <<'HELP'
Usage: ./version.sh [show | check | set VERSION | bump KIND | preview KIND]
  show          Display the authoritative project version (default).
  check         Verify the version and dependency lockfile offline.
  set VERSION   Set an explicit PEP 440 version and update uv.lock.
  bump KIND     Update the version and lockfile using uv bump semantics.
  preview KIND  Preview a bump without writing files.
Kinds: major, minor, patch, stable, alpha, beta, rc, post, dev.
HELP
}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
mode=${1:-show}
case "$mode" in
    -h|--help) usage; exit 0;;
    show|check) [[ $# -le 1 ]] || die 'Unexpected arguments';;
    set|bump|preview) [[ $# == 2 && -n $2 ]] || die 'Expected exactly one value';;
    *) usage >&2; exit 1;;
esac
command -v uv >/dev/null || die 'uv unavailable; run setup.sh first.'
[[ -f pyproject.toml ]] || die 'Missing pyproject.toml.'
case "$mode" in
    show) uv version --short --offline;;
    check) uv version --short --offline; uv lock --check --offline;;
    bump|preview)
        case "$2" in major|minor|patch|stable|alpha|beta|rc|post|dev) ;; *) die 'Invalid bump kind';; esac
        if [[ $mode == preview ]]; then
            uv version --bump "$2" --dry-run --no-sync --offline
            exit
        fi;;
esac
if [[ $mode == set || $mode == bump ]]; then
    [[ -f uv.lock ]] || die 'Missing uv.lock; run setup.sh first.'
    uv lock --check --offline
    backup=$(mktemp -d)
    cp -p pyproject.toml uv.lock "$backup/"
    committed=false
    cleanup() {
        if [[ $committed != true ]]; then
            cp -p "$backup/pyproject.toml" "$backup/uv.lock" . || { printf 'Restore failed; backups retained at %s\n' "$backup" >&2; return 1; }
        fi
        rm -rf -- "$backup"
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    if [[ $mode == set ]]; then
        uv version --no-sync -- "$2"
    else
        uv version --no-sync --bump "$2"
    fi
    uv lock --check --offline
    committed=true
fi
