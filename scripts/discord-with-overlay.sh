#!/usr/bin/env bash
# Managed VencOverlay host launcher. Keep the overlay outside the Flatpak sandbox.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is required}"
lock="$XDG_RUNTIME_DIR/vencoverlay-discord.lock"
[[ ! -L "$lock" ]] || { echo 'Unsafe VencOverlay lock' >&2; exit 1; }
run=(flatpak run --@SCOPE@ --branch=stable --arch=@ARCH@ --command=com.discordapp.Discord --file-forwarding com.discordapp.Discord)
running() {
    local apps
    apps=$(flatpak ps --columns=application) || return 2
    grep -Fxq com.discordapp.Discord <<<"$apps"
}
# Append rather than truncate the shared installation/launcher lock.
exec 9>>"$lock"
if ! flock -n 9; then
    if running; then
        exec 9>&-
        exec "${run[@]}" "$@"
    fi
    echo 'VencOverlay installation or launch is already in progress.' >&2
    exit 1
fi
if running; then
    # An externally started Discord instance is not ours to supervise.
    exec 9>&-
    exec "${run[@]}" "$@"
else
    status=$?
    if ((status != 1)); then
        echo 'Cannot inspect running Discord instances.' >&2
        exit 1
    fi
fi
"$ROOT/vencoverlay" 9>&- &
overlay_pid=$!
cleanup() {
    # Only signal a job still owned by this shell, not a recycled process ID.
    if jobs -pr | grep -Fxq "$overlay_pid"; then
        kill -TERM "$overlay_pid" 2>/dev/null || true
    fi
    wait "$overlay_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Flatpak forwards desktop URI markers; do not start another overlay for them.
"${run[@]}" "$@" 9>&- &
discord_pid=$!
wait "$discord_pid"
