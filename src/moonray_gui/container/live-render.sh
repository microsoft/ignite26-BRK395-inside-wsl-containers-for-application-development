#!/bin/bash
# live-render.sh - LIVE progressive MoonRay stream for the WPF viewer.
#
# This is the piece that makes the viewer show a render "coming in" - patches and
# noise clearing over time - instead of rendering a whole frame and swapping it in.
# It mirrors the arras ProgressiveFrame viewer (moonray_windows/basic/main.cc), but
# uses moonray_gui's own live framebuffer:
#
#   1. moonray_gui runs headless (QT_QPA_PLATFORM=offscreen) with a debug console.
#   2. Over the console we send `shmFbOutput active on`, enabling moonray_gui's
#      shared-memory framebuffer (the exact image it would show on screen).
#   3. `shmFbDump <id> live.ppm` snapshots that LIVE framebuffer many times a second
#      so the Windows app can watch the image refine in real time.
#
# Camera moves are LIVE, exactly like moonray_gui itself: moonray_gui is launched ONCE
# with `-deltas <file>` and left running. When that file changes, moonray_gui applies
# an in-process incremental updateScene() - a camera-only node_xform change is not
# geometry, so it simply re-renders from the new view (no scene reload, no relaunch).
#
# The catch: moonray's ChangeWatcher is inotify-based, and inotify does NOT observe
# host-side writes on the /mnt/out bind mount. So the deltas file lives on a guest-local
# path (/tmp/cam_delta.rdla) and a tiny "camera pump" mirrors the host's cam_delta.rdla
# into it. Only a resolution change (detail slider) still relaunches moonray_gui.
#
# Control files (all under the bind-mounted /mnt/out):
#   res.txt            resolution divisor (higher = coarser/faster). Default 4.
#   cam_delta.rdla     current camera transform (orbit + pan), rewritten on every move;
#                      the host writes it in place and terminates it with a "--eof" line.
#   reload.flag        touched by the app to request a relaunch (resolution change only).
#   live.ppm           output: the latest live framebuffer (P3 PPM).

set -uo pipefail
export QT_QPA_PLATFORM=offscreen
OUT=/mnt/out
SCENE=/mnt/scene
PORT=30791
# The deltas file MUST live on a guest-local filesystem: moonray_gui watches it with inotify,
# and inotify does not observe host-side writes on the /mnt/out bind mount.
DELTA=/tmp/cam_delta.rdla
# Snapshot of the camera used as an -in seed at each (re)launch. It is watched by moonray's
# *scene* watcher, so it must stay static while moonray runs (only -deltas may change live,
# otherwise a full reload would be triggered). We refresh it only just before a launch.
START=/tmp/cam_start.rdla
cd "$SCENE"

read_res() {
    RES=$(cat "$OUT/res.txt" 2>/dev/null)
    case "$RES" in ''|*[!0-9]*) RES=4 ;; esac
}

# Mirror the host's cam_delta.rdla (rewritten on every orbit/pan) into the guest-local deltas
# file. Rewriting DELTA fires moonray_gui's inotify watcher, which applies the new camera
# in-process (updateScene) - no scene reload, no relaunch. The host writes cam_delta.rdla in
# place, so we only mirror a copy that ends with the "--eof" sentinel (i.e. a complete write).
cam_pump() {
    local last="" cur
    while true; do
        cur=$(cat "$OUT/cam_delta.rdla" 2>/dev/null || true)
        case "$cur" in
            *--eof)
                if [ "$cur" != "$last" ]; then
                    if printf '%s\n' "$cur" > "$DELTA.tmp" 2>/dev/null; then
                        mv -f "$DELTA.tmp" "$DELTA" 2>/dev/null && last="$cur"
                    fi
                fi
                ;;
        esac
        sleep 0.04
    done
}

# Seed the deltas file from the host's initial camera (static at startup, so a plain copy is safe).
[ -f "$OUT/cam_delta.rdla" ] && cp -f "$OUT/cam_delta.rdla" "$DELTA" 2>/dev/null || : > "$DELTA"
cam_pump &
PUMP=$!
trap 'kill "$PUMP" 2>/dev/null || true' EXIT

while true; do
    rm -f "$OUT/reload.flag" 2>/dev/null || true
    read_res

    # Freeze the current camera as the -in seed so orbit/pan survive a resolution relaunch.
    cp -f "$DELTA" "$START" 2>/dev/null || : > "$START"

    INPUTS=()
    [ -f scene.rdlb ] && INPUTS+=(-in scene.rdlb)
    [ -f scene.rdla ] && INPUTS+=(-in scene.rdla)
    [ -s "$START" ] && INPUTS+=(-in "$START")

    echo "[live] moonray_gui res=$RES ${INPUTS[*]} -deltas $DELTA"
    moonray_gui "${INPUTS[@]}" -deltas "$DELTA" -res "$RES" -debug_console "$PORT" > "$OUT/gui.log" 2>&1 &
    GUI=$!

    # Wait for the debug console to come up (or the process to die).
    READY=0
    for _ in $(seq 1 200); do
        sleep 0.1
        grep -q "threadMain() booted" "$OUT/gui.log" 2>/dev/null && { READY=1; break; }
        kill -0 "$GUI" 2>/dev/null || break
    done

    CTRL=0
    if [ "$READY" = 1 ]; then
        # Enable the shared-memory framebuffer output over the telnet console.
        exec 3<>"/dev/tcp/127.0.0.1/$PORT" && printf 'shmFbOutput active on\n' >&3

        # Discover this instance's shm control-segment id. moonray_gui stays alive for the whole
        # session (camera moves are deltas, not relaunches), so gui.log holds a single stable id.
        CTRL=""
        for _ in $(seq 1 100); do
            sleep 0.1
            CTRL=$(grep -oE 'ShmDataManager shmId:[0-9]+' "$OUT/gui.log" 2>/dev/null | head -1 | grep -oE '[0-9]+$')
            [ -n "$CTRL" ] && break
            kill -0 "$GUI" 2>/dev/null || break
        done
        [ -z "$CTRL" ] && CTRL=0
        echo "[live] shmFb control id=$CTRL"
    fi

    # Stream the live framebuffer. Camera moves are applied in-process via -deltas; we only
    # break to relaunch when a resolution change (detail slider) requests a reload.
    while kill -0 "$GUI" 2>/dev/null; do
        [ -f "$OUT/reload.flag" ] && break
        if shmFbDump "$CTRL" "$OUT/.live_tmp.ppm" >/dev/null 2>&1 && [ -s "$OUT/.live_tmp.ppm" ]; then
            mv -f "$OUT/.live_tmp.ppm" "$OUT/live.ppm"
        fi
        sleep 0.06
    done

    exec 3<&- 2>/dev/null || true
    kill "$GUI" 2>/dev/null || true
    wait "$GUI" 2>/dev/null || true

    # Reclaim the just-killed instance's leaked System V shm segments so many relaunches
    # don't fill up shared memory (no other process in this container uses them).
    ipcs -m 2>/dev/null | awk '$2 ~ /^[0-9]+$/ {print $2}' | while read -r _id; do
        ipcrm -m "$_id" 2>/dev/null || true
    done
done
