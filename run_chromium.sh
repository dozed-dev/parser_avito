#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
HOST_WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
HOST_XAUTHORITY="${XAUTHORITY:-$(find "$HOST_XDG_RUNTIME_DIR" -maxdepth 1 -type f -name 'xauth_*' -print -quit)}"

if [[ ! -f "$HOST_XAUTHORITY" ]]; then
    echo "Xauthority file not found: set XAUTHORITY" >&2
    exit 1
fi

exec docker run --rm -it \
    --ipc=host \
    --network=host \
    --hostname "$(hostname)" \
    --userns=keep-id \
    --user="$(id -u):$(id -g)" \
    --device /dev/dri \
    -e DISPLAY \
    -e "WAYLAND_DISPLAY=$HOST_WAYLAND_DISPLAY" \
    -e XDG_RUNTIME_DIR=/tmp \
    -e XAUTHORITY=/tmp/Xauthority \
    -e HOME=/tmp \
    -v "$HOST_XDG_RUNTIME_DIR/$HOST_WAYLAND_DISPLAY:/tmp/$HOST_WAYLAND_DISPLAY" \
    -v /tmp/.X11-unix:/tmp/.X11-unix:ro \
    -v "$HOST_XAUTHORITY:/tmp/Xauthority:ro" \
    -v "$ROOT_DIR:/app:Z" \
    --entrypoint /opt/avito-venv/bin/python \
    localhost/parser_avito:latest \
    /app/run_chromium.py
