#!/bin/bash
# arras-render
#
# Convenience wrapper for the *full* arras interactive path inside the container.
#
# The openmoonray image bundles the arras distributed-rendering stack (the same
# code as arras4_core in this repo). `arras_render` is the reference interactive
# client; in "-local" mode it spins up a coordinator + mcrt computation locally
# (see arras4_client/lib/client/local/LocalSession.cc) with no external services.
#
# arras_render is a Qt GUI application. Under WSL it renders through WSLg, so the
# window appears on the Windows desktop. This script is provided so the arras path
# is a single command away; the primary, fully-native Windows experience is the
# MoonrayArrasViewer C# app, which drives `moonray-progressive` and displays frames
# in a WPF window (no WSLg dependency).
#
# Usage:
#   arras-render <scene_dir> [extra arras_render args...]

set -euo pipefail

SCENE_DIR="${1:?scene_dir required}"
shift || true

cd "$SCENE_DIR"

if ! command -v arras_render >/dev/null 2>&1; then
    echo "arras_render is not on PATH in this image."
    echo "It ships with a full openmoonray build; ensure the arras components were built."
    exit 127
fi

# -local runs a self-contained arras session (coordinator + mcrt computation) in
# this container. Requires a display (WSLg) for the Qt viewer.
exec arras_render -local \
    -in scene.rdlb -in scene.rdla \
    "$@"
