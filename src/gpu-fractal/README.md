# CUDA Canvas

A WSLC GPU demo: a Windows browser requests a Mandelbrot fractal, a CUDA kernel
inside a Linux container computes every pixel, and the browser displays the result.
Click to zoom, dive into a spiral, or animate 20 GPU-computed frames.

## Requirements

- WSLC with GPU support on an NVIDIA CUDA-capable Windows machine.
- A Windows NVIDIA driver with WSL CUDA support.
- Internet access for the initial image build.

This demo uses NVIDIA CUDA through WSL's GPU paravirtualization, not exclusive
PCIe device assignment. It shares the Windows GPU with the container. It does not
support AMD or Intel GPUs, and has no CPU rendering fallback.

## Build and run

From the repository root in PowerShell:

```powershell
cd src\gpu-fractal
wslc build --progress plain -f Containerfile -t gpu-fractal:demo .
wslc run -d --name gpu-fractal --hostname cuda-canvas --gpus all -p 127.0.0.1:8090:8090 --mount "type=bind,source=$($PWD.Path),target=/app,readonly" gpu-fractal:demo
```

Open **http://127.0.0.1:8090**. The first page load renders a real GPU frame.

The Containerfile installs Python and curl. WSLC exposes `/dev/dxg` and the NVIDIA
CUDA driver when `--gpus all` is supplied. The application uses Python's `ctypes`
to launch an embedded PTX kernel through that driver, so the image needs neither
the CUDA toolkit nor a machine-learning framework.

The bind mount exposes the current Windows demo folder as `/app` inside Linux,
read-only. It hides the copy of `/app` baked into the image while the mount is
present: the image supplies Python and its dependencies, while the Windows folder
supplies the application code. Restart the container after editing the Python
file on Windows to load the updated code; no image rebuild is required.

If a previous demo container still exists, remove it with
`wslc remove -f gpu-fractal` before running the command again.

## Show the flow

To show why GPU exposure matters, first run without it:

```powershell
wslc run --rm gpu-fractal:demo
```

This is expected to exit with an explicit CUDA initialization error, not switch
to CPU rendering. Then use the run command above with `--gpus all`.

1. Show GPU exposure (`--gpus all`), port mapping (`-p`), and the read-only Windows-folder mapping (`--mount`).
2. Show the NVIDIA GPU name, Linux kernel, `/dev/dxg`, and socket on the page.
3. Click **DIVE INTO THE SPIRAL** or click a point on the fractal to zoom.
4. Click **ANIMATE** to request 20 successive GPU frames.
5. Point out the CUDA kernel timing: CUDA events measure GPU execution, not JavaScript animation.

Each frame launches 368,640 CUDA threads, one per pixel. Rendering is limited to
one frame at a time and at most 1,024 Mandelbrot iterations per pixel. GPU time
excludes copying pixels back and sending them to the browser; the page reports
those timings separately. This is a visual compute demo, not a standardized GPU benchmark.

In another terminal:

```powershell
wslc exec gpu-fractal /usr/lib/wsl/lib/nvidia-smi
wslc logs -f gpu-fractal
```

Talk track: "The image supplies the runtime, the Windows bind mount supplies the
application code, and WSLC exposes the GPU through WSL. This browser request crosses
the published port, launches CUDA threads inside Linux, and brings real
GPU-computed pixels back to Windows."

## Cleanup

```powershell
wslc remove -f gpu-fractal
```
