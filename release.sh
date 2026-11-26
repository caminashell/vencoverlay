#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Resolve a verification path relative to the caller, not the project root.
if [[ ${1:-} == --verify && $# == 2 ]]; then
    archive=$(realpath -- "$2")
    cd -- "$ROOT"
    exec python3 -m scripts.release_package --verify "$archive"
fi
if [[ ${1:-} == --verify-sources && $# == 2 ]]; then
    archive=$(realpath -- "$2")
    cd -- "$ROOT"
    exec python3 -m scripts.release_sources --verify "$archive"
fi
cd -- "$ROOT"
if [[ ${1:-} == --audit-licences && $# == 1 ]]; then
    exec python3 -m scripts.release_licences audit
fi
if [[ ${1:-} == --collect-sources && $# == 1 ]]; then
    exec python3 -m scripts.release_sources
fi
exec python3 -m scripts.release_package "$@"
