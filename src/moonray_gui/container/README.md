# container — the shared `openmoonray:latest` image

Builds the container image that both Windows apps run. It is a from-source
**OpenMoonRay** build on **Rocky Linux 9** (following the upstream
[Rocky 9 container recipe](https://docs.openmoonray.org/getting-started/installation/building-moonray/rocky9_container_build/)),
which bundles MoonRay *and* the arras distributed-rendering stack.

## Build

```powershell
./build-image.ps1
```

Runs `wslc build -t openmoonray:latest` then `wslc save` → `moonray.tar`. Output is
streamed to the console and to `build.log`. **This is a source build and can take tens
of minutes to a few hours the first time**; subsequent builds reuse the layer cache.

## Contents

| File | Purpose |
|------|---------|
| `Containerfile` | Multi-stage OpenMoonRay source build; installs helper scripts; sets `ENTRYPOINT` to `/entrypoint.sh`. |
| `nobind.c` | `LD_PRELOAD` shim that turns `mbind()`/`move_pages()` into no-ops (WSLc containers block those NUMA syscalls). |
| `live-render.sh` | The live-render controller used by the viewer: launches `moonray_gui` offscreen **once** with `-deltas`, enables its shared-memory framebuffer over the debug console, dumps the live framebuffer to `live.ppm` (~15×/s), and runs a camera pump that mirrors host camera writes into the guest-local deltas file so orbit/pan update **in-process** (no restart). Only a resolution change relaunches. The C# app also embeds this script and writes it to the bind-mounted out dir at runtime, so **no image rebuild is needed** to change it. |
| `moonray-progressive.sh` | *(legacy)* `moonray-progressive <scene> <out> [divisors...]` — renders one frame per resolution divisor (coarse→fine). Superseded by `live-render.sh`; kept as a simple reference. |
| `arras-render.sh` | Wrapper documenting the `arras_render -local` interactive path (WSLg GUI). |
| `build-image.ps1` | Runs `wslc build` + `wslc save`, logging to `build.log`. |
| `.dockerignore` | Restricts the build context to the files above. |

## Image entry points

The image `ENTRYPOINT` is `/entrypoint.sh`, which sources MoonRay's `setup.sh` (setting
`PATH`, `RDL2_DSO_PATH`, etc.) then `exec "$@"`. The SDK apps run commands via
`CreateProcess` (exec), which bypasses the image `ENTRYPOINT`, so they invoke it
explicitly, e.g. `["/entrypoint.sh", "moonray", ...]` or
`["/entrypoint.sh", "bash", "/mnt/out/live-render.sh"]`.

Exposed commands inside the image:

- `moonray_gui` — progressive tiled renderer with a shared-memory framebuffer + debug
  console; driven headless by `live-render.sh` for the viewer's live stream, with live
  camera updates fed through its `-deltas` hot-reload path.
- `shmFbDump` — snapshots `moonray_gui`'s live shared framebuffer to a PPM.
- `moonray` — batch renderer.
- `arras_render` — arras interactive client (GUI via WSLg; see `arras-render.sh`).

## Alternative: build the image from MSBuild

The 2.9.3 package also ships a `WslcImage` MSBuild item (build + save via the `wslc`
CLI). We keep a standalone `build-image.ps1` here because the source build is long and
should not run on every project build, but a consuming project could instead add:

```xml
<ItemGroup>
  <WslcImage Include="openmoonray"
             Dockerfile="container\Containerfile"
             Context="container" />
</ItemGroup>
```
