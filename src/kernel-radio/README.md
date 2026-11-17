# Kernel Radio

A small WSL containers demo: a Windows browser talks to a Python web server in a
Linux container through a published port. Click **Send CPU pulse** to start a
Linux process that keeps one CPU busy for four seconds. The dashboard shows
real readings from Linux `/proc`, including the kernel version, the container's
processes, WSL VM memory use, and the socket on each side of the port forward.

## Requirements

- Windows with WSL and WSL containers (`wslc`).
- Internet access for the initial image build.

## Run

From this folder in PowerShell:

```powershell
wslc build --progress plain -f Containerfile -t kernel-radio:demo .
wslc run -d --name kernel-radio -p 127.0.0.1:8000:8000 kernel-radio:demo
```

Open <http://localhost:8000>.

## Explore

```powershell
wslc logs -f kernel-radio
wslc exec kernel-radio uname -r
```

Each CPU pulse logs a line with its Linux process ID and the request tag shown
in the browser.

## Clean up

```powershell
wslc remove -f kernel-radio
```
