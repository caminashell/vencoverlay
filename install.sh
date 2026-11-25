#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$ROOT"
if [[ ${1:-} == --check ]]; then
    shift
    exec python3 -m scripts.flatpak_install check "$@"
fi
exec python3 -m scripts.flatpak_install install "$@"
