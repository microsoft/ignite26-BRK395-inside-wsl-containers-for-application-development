#!/bin/bash
# moonray-progressive
#
# Renders a MoonRay scene as a sequence of progressively refining frames, so an
# interactive client (the Windows C# "MoonrayArrasViewer") can display each frame
# as soon as it is written and watch the image sharpen over time.
#
# This mirrors the experience of the arras ProgressiveFrame viewer
# (moonray_windows/basic/main.cc), where a client receives increasingly refined
# frames from the mcrt computation -- except here the same MoonRay rendering engine
# runs headless inside the WSL container and drops each pass to a bind-mounted
# folder that the native Windows app is watching.
#
# Usage:
#   moonray-progressive <scene_dir> <out_dir> [res_divisors...]
#
#   scene_dir     Directory containing scene.rdla / scene.rdlb (bind-mounted).
#   out_dir       Directory to write frame_NN.jpg into (bind-mounted).
#   res_divisors  Optional space-separated list of resolution divisors, coarse to
#                 fine. Higher divisor = lower resolution = faster. Defaults to
#                 "16 8 4 2 1".
#
# A "done.txt" marker file is written into out_dir when all passes complete.

set -euo pipefail

SCENE_DIR="${1:?scene_dir required}"
OUT_DIR="${2:?out_dir required}"
shift 2 || true

RES_DIVISORS=("$@")
if [ "${#RES_DIVISORS[@]}" -eq 0 ]; then
    RES_DIVISORS=(16 8 4 2 1)
fi

mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR"/frame_*.jpg "$OUT_DIR/done.txt" "$OUT_DIR/error.txt" 2>/dev/null || true

cd "$SCENE_DIR"

INPUTS=()
[ -f scene.rdlb ] && INPUTS+=(-in scene.rdlb)
[ -f scene.rdla ] && INPUTS+=(-in scene.rdla)
if [ "${#INPUTS[@]}" -eq 0 ]; then
    echo "no scene.rdla/scene.rdlb found in $SCENE_DIR" | tee "$OUT_DIR/error.txt"
    exit 2
fi

trap 'echo "render failed" | tee "$OUT_DIR/error.txt"' ERR

i=0
total="${#RES_DIVISORS[@]}"
for RES in "${RES_DIVISORS[@]}"; do
    idx=$(printf '%02d' "$i")
    OUT="$OUT_DIR/frame_${idx}.jpg"
    echo "[progressive] pass $((i+1))/$total : resolution divisor $RES -> $OUT"
    moonray "${INPUTS[@]}" -out "$OUT" -res "$RES"
    echo "[progressive] wrote $OUT"
    i=$((i+1))
done

echo "done" > "$OUT_DIR/done.txt"
echo "[progressive] all $total passes complete"
