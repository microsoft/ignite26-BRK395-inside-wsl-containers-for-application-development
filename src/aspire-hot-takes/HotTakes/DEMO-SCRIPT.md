# 🔥 Hot Takes — Aspire on WSLc (2-minute demo)

**One-liner:** *Aspire runs your whole distributed app — code, databases and caches — with one command, and now its Linux containers can run on **WSLc**, the container runtime built into Windows. No Docker Desktop required.*

```
browser ──► web (ASP.NET) ──► api (ASP.NET) ──► Redis   (container on WSLc)
                                 │          └─► Postgres (container on WSLc)
                                 └─ HotTakeBot ─► npu-agent (container on WSLc, --gpus all,
                                                             Qwen3-8B LLM on the Intel NPU)
```

---

## What you need to know (cheat sheet)

| | |
|---|---|
| **Aspire** | Open-source toolkit: describe your app in code (the *AppHost*), and `aspire run` starts every service + container, wires up connection strings/service discovery, and gives you a dashboard with logs, traces and metrics (OpenTelemetry). |
| **WSLc** | WSL containers (`wslc.exe`), Microsoft's built-in Linux container runtime for Windows. GA Sep 2026. |
| **The new feature** | Aspire 17.0 recognizes WSLc as a first-class container runtime for local dev. Auto-detection order is **Docker → Podman → WSLc**. Force it with `ASPIRE_CONTAINER_RUNTIME=wslc`. `aspire doctor` shows which runtime is active. |
| **NPU agent (bonus)** | `npu-agent` is a Python + OpenVINO GenAI container that Aspire **builds from a Dockerfile** (`AddDockerfile`) and runs on WSLc with `--gpus all` (`WithContainerRuntimeArgs`). That flag passes the GPU **and the Intel NPU** into the Linux container. It serves **Qwen3-8B (int4)** on the NPU (≈1.3 s to the first word, ≈2.5 s per take). The **Hot Take Bot** (a background service in `api`) reads the current takes, asks the NPU for a new one, posts it to Postgres and clears the Redis cache. One trace covers the whole thing. If no NPU is visible it falls back to CPU, and the UI says so. |
| **Limits (be honest if asked)** | Aspire 17.0 is in **preview** (daily channel). Needs WSLc ≥ 3.0.2.0. It covers local dev on Windows; **publish/deploy with WSLc isn't supported yet** (PR in progress). It doesn't work when Aspire itself runs *inside* a WSL distro. Containers reach the host via `host.wslc.internal`. |

---

## Pre-show checklist (do this 10+ minutes before)

1. `cd C:\cDev\ignite26-brk395\src\aspire-hot-takes\HotTakes`
2. Run **`.\demo-prep.ps1`**. It builds, pre-pulls `postgres:18.3` + `redis:8.6` into WSLc, checks that the `openvino:2026.2.1` base image is loaded, downloads the NPU model into `models\` if needed (one time, 4.4 GB), does a full warm-up run, has the bot write one take, and stops. It must end with `Ready (incl. NPU agent…)`, show `database (~300 ms)` / `cache (~5 ms)`, and print **`NPU agent wrote a take on NPU (Intel(R) AI Boost)`** in green. Yellow `on CPU` means the NPU isn't visible; see Troubleshooting.
3. Set up the screen:
   - **VS Code** on `HotTakes.AppHost\AppHost.cs`, zoomed in (`Ctrl+=` ×3).
   - **Terminal A** (big font) in the `HotTakes` folder. Run `. .\demo-env.ps1` and then `cls`.
   - **Terminal B** (big font), same folder, `. .\demo-env.ps1`, `cls`. This is for `wslc list`.
   - **Browser** (zoom 125%). Close other tabs.
   - *(For the NPU bonus)* **Task Manager → Performance → NPU** in a small window, ready to Alt+Tab to.
4. Make sure nothing is left from earlier runs: `aspire ps` should show no AppHost and `wslc list` should be empty.
5. Turn on Do Not Disturb.

> ℹ️ **Why `--non-interactive`?** Interactive `aspire run` offers to trust the HTTPS dev certificate, which pops a Windows dialog. This demo is HTTP-only on purpose, so `--non-interactive` skips that prompt (you'll see one harmless ⚠️ line about certificates). If you'd rather use plain `aspire run`, run `aspire certs trust` once before the show and click **Yes**.

---

## The script (~2:00)

### 0:00 – 0:20 · Kick it off, then explain Aspire  *(Terminal A → VS Code)*

**Type in Terminal A:**
```powershell
aspire run --non-interactive
```

**Switch to VS Code (`AppHost.cs`) while it starts and say:**
> "This is a little voting app for developer hot takes. It has a web frontend, an API, a Postgres database and a Redis cache.
> With **Aspire**, this one short C# file *is* the whole architecture. Postgres and Redis are real Linux containers. The API **references** the database and cache and **waits for** them. The web app references the API.
> No docker-compose, no connection strings, no hard-coded ports. Aspire wires all of it up."

### 0:20 – 0:40 · The WSLc part  *(Terminal B)*

**Type:**
```powershell
aspire doctor
```
Point at **`✅ WSLc v3.x: running ← active`**.

> "And here's the new part. This machine has **no Docker Desktop**…"

```powershell
Get-Command docker
```
*(it errors)*

> "…Aspire found **WSLc**, the container runtime built right into Windows, and is using it automatically."

```powershell
wslc list
```
> "There are my Postgres and Redis containers, running on WSLc." *(You'll also see `npu-agent`. Save it for the bonus, or just say "and an AI one we'll get to".)*

### 0:40 – 1:00 · The dashboard  *(Terminal A → browser)*

**Ctrl+click the `Dashboard:` login link in Terminal A.**

> "This is the Aspire dashboard. Here's every resource: the containers and the two projects, all **Running / Healthy**. Aspire started them in dependency order."

*(`npu-agent` shows **Unhealthy** for its first ~15 s while it loads the 8B model onto the NPU. That's expected and doesn't block anything.)*

- Optional: click the **Graph** toggle to show web → api → postgres/redis.
- Click the **web** URL (`http://localhost:5050`).

### 1:00 – 1:20 · Use the app  *(browser, Hot Takes tab)*

> "Here's the app. Look at the badge: the first load came from **Postgres** in about 300 ms."

- Click **↻ Refresh**. The badge turns green: **⚡ Served from Redis cache in ~2 ms**.
- Click **🔥 Vote** on *"Windows is the best place to run Linux containers"*. The vote clears the cache, so the next load comes from Postgres again.

> "Votes go to Postgres, reads are cached in Redis. A classic cache-aside pattern."

### 1:20 – 1:50 · Observability for free  *(dashboard tab)*

**Click `Traces`.**

> "Because Aspire turned on OpenTelemetry everywhere, I can see **every request end to end**."

- Open a **long `web: GET /leaderboard`** trace (~0.3 s). It shows web → api → Redis `GET` (miss) → **`postgresql` 300 ms** → Redis `SETEX`.
- Open a **short one** (a few ms). It shows web → api → Redis `GET`, and that's it.

> "There's the cache miss hitting the database, and the cache hit that never touches it. That's real Redis and real Postgres, in Linux containers on WSLc."

**Click `Structured logs`.** Show `Leaderboard served from Redis cache in 1 ms` and `Vote recorded for take 3…`. Point out that the values are searchable fields, not just text.

### 1:50 – 2:00 · Wrap  *(Terminal A)*

**Press `Ctrl+C`** in Terminal A, then in Terminal B:
```powershell
wslc list
```
*(empty; the containers were cleaned up after a few seconds)*

> "One command up, one keystroke down. **Aspire** gives you the orchestration, service discovery and observability. **WSLc** gives you Linux containers built into Windows. Together they're the whole local dev loop with no Docker Desktop. Try it with `aspire doctor` on Aspire 17."

---

## Bonus beat: AI on the NPU (~45 s)  *(VS Code → browser → Task Manager → dashboard)*

**VS Code, `AppHost.cs`: highlight the `npu-agent` block.**

> "One more resource. `npu-agent` is a Python container running an **8-billion-parameter LLM**. Aspire **builds it from a Dockerfile** and runs it on WSLc, and this one line, **`--gpus all`**, passes the **NPU** through into the Linux container."

**Browser (Hot Takes tab):** the bot panel chip reads **🧠 Qwen3-8B ready on the NPU · Intel(R) AI Boost**.

- Click **🤖 Write a take**. **Alt+Tab to Task Manager** right away. The **NPU graph spikes**.
- Back in the browser, a new take slides in with the tag **🤖 written by AI on the NPU in ~2.5 s**. The "Last take" line shows the model and the time to the first word.

> "That's a brand-new hot take, written by a local 8B model running on the **NPU**, inside a Linux container on Windows, orchestrated by Aspire. Nothing went to the cloud."

**Dashboard → Traces**, search **`HotTakeBot cycle`**, and open it:

> "And it's one trace: web → api → read the takes from Postgres → **POST to npu-agent (~2.3 s)** → insert → clear the Redis cache. The span is tagged with `npu.device=NPU`, the model, the time to the first token and the token count."

Optional: flip **Autopilot** on. The bot posts a take every 20 s (5 max, then it turns itself off) and the countdown is shown.
> ⚠️ Every bot post clears the Redis cache. That's why Autopilot is **off by default**: so it can't mess up the cache-hit beat.

**Honest notes if asked:** Aspire 17 + WSLc are preview. NPU passthrough to WSLc containers is new. The first-ever NPU compile of the model takes ~50 s (prep does it; the result is cached in `models\.cache`). After that, a load takes ~13 s, which happens in the background while the rest of the app is already usable. Without an NPU, the agent falls back to CPU and the UI shows a ⚠️ "No NPU visible" chip.

---

## More bonus beats (if you have extra time)

- **Resilience:** in the dashboard, use **⋯ → Stop** on `cache`. The app shows an error badge. **Start** it again and click Refresh; it recovers.
- **No config:** click the `api` resource → **Environment variables** to show `ConnectionStrings__hottakesdb` and `ConnectionStrings__cache`, which Aspire injected.
- **Agent/CLI friendly:** `aspire describe` and `aspire otel traces web` show the same resource and trace data in the terminal.
- **Forcing the runtime:** `$env:ASPIRE_CONTAINER_RUNTIME='wslc'; aspire doctor` shows `← explicitly selected`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `aspire doctor` shows Docker/Podman active instead of WSLc | `. .\demo-env.ps1` pins WSLc automatically when Docker/Podman is on PATH, or set `$env:ASPIRE_CONTAINER_RUNTIME='wslc'`. |
| `aspire doctor` shows no runtime / old Aspire | WSLc support needs the **17.0 daily** CLI: `aspire --version` should be `17.0.0-preview…`. Reinstall: `iex "& { $(irm https://aspire.dev/install.ps1) } -Quality dev"` |
| `dotnet` says no SDK found | Dot-source `.\demo-env.ps1` (it adds the user-local .NET 10 SDK at `%LOCALAPPDATA%\Microsoft\dotnet`). |
| A Windows "Security Warning" dialog appears | You ran `aspire run` without `--non-interactive`. Click **No** (or run `aspire certs trust` beforehand and click **Yes**). |
| Dashboard asks for a token | Use the `login?t=…` link printed by `aspire run`. |
| Start-up is slow (> 30 s) | Images weren't cached. Re-run `.\demo-prep.ps1`. |
| Port 5050 / 15204 already in use | Something is left over: `aspire stop`, then `aspire ps`. |
| Need a clean slate (votes back to 0) | `Ctrl+C` and `aspire run --non-interactive` again. Postgres is a fresh container on every run. |
| Stuck containers after a crash | `wslc list`, then `wslc rm -f <id>`. |
| Prep: `openvino:2026.2.1 is not loaded` | `wslc load -i openvino-2026.2.1.tar` (the same base image `..\npu-test` uses). |
| Bot chip stays "warming up" | `aspire logs npu-agent`. If the model is missing, re-run `.\demo-prep.ps1` (it downloads `models\Qwen3-8B-int4-cw-ov`). If `models\.cache` was deleted, the first load recompiles (~50 s). |
| Bot chip says **⚠️ No NPU visible** | The NPU isn't visible in the container (`devices visible in container: ['CPU']` in the logs). Check the NPU driver and run `wslc run --rm --gpus all openvino:2026.2.1 python3 -c "import openvino as ov; print(ov.Core().available_devices)"`, which should list `NPU`. The demo still works on CPU (~3 s per take). |
| Need a smaller/different model | Set `NpuAgent:Model` in `HotTakes.AppHost\appsettings.json` to another `OpenVINO/*-int4-cw-ov` model and re-run prep. |

---

## What's in the folder

| Path | What it is |
|---|---|
| `HotTakes.AppHost/AppHost.cs` | The whole architecture: Postgres + Redis containers, the npu-agent Dockerfile container (`--gpus all`), api, web. |
| `HotTakes.Api/Program.cs` | `GET /takes` (Redis cache-aside over Postgres, with a deliberately slow 300 ms query), `POST /takes/{id}/vote`, and the `/bot` endpoints. |
| `HotTakes.Api/HotTakeBot.cs` | The Hot Take Bot agent loop (observe → think on the NPU → act), its autopilot background service, and the npu-agent client. |
| `HotTakes.NpuAgent/` | Dockerfile (`openvino:2026.2.1` + OpenVINO GenAI) and `app/server.py`, a small HTTP server that loads the LLM on NPU (CPU fallback): `/health`, `/status`, `/generate`. |
| `models/` | The downloaded model (`Qwen3-8B-int4-cw-ov`, ~4.4 GB) plus the compiled NPU blob in `.cache/`. **Not source**: created by `demo-prep.ps1` and bind-mounted into npu-agent. |
| `HotTakes.Web/` | Minimal ASP.NET app. Serves `wwwroot/index.html` and calls the API via service discovery (`https+http://api`). |
| `HotTakes.ServiceDefaults/` | Standard Aspire defaults: OpenTelemetry, health checks, resilience, service discovery. |
| `demo-env.ps1` | Dot-source it. Puts the SDK and Aspire CLI on PATH and pins WSLc if Docker/Podman exist. |
| `demo-prep.ps1` | Pre-show build, image pre-pull, model download, and a warm-up run (including one NPU generation). |

**Tested with:** Aspire CLI/SDK 17.0.0-preview.1.26527.4 (daily), .NET SDK 10.0.403, WSLc 3.0.4.0, OpenVINO + GenAI 2026.2.1, Intel Core Ultra X7 368H (Intel AI Boost NPU), Windows, without Docker or Podman installed.
