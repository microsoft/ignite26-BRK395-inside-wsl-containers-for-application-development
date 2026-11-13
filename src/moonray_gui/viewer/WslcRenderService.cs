using System.Collections.Concurrent;
using System.IO;
using System.Text;
using Microsoft.WSL.Containers;

namespace MoonrayArrasViewer;

/// <summary>
/// Drives LIVE progressive MoonRay rendering inside a WSL container using the C# projection
/// of the WSL Container SDK (Microsoft.WSL.Containers).
///
/// This is the native-Windows counterpart of the arras interactive viewer
/// (moonray_windows/basic/main.cc). Instead of a distributed mcrt computation streaming
/// ProgressiveFrame messages, we run moonray_gui headless in the container and tap its own
/// shared-memory framebuffer: a small controller script (live-render.sh) enables shmFb and
/// dumps the LIVE framebuffer to /mnt/out/live.ppm many times a second. The service polls
/// that file and raises <see cref="FrameReady"/> for each refined frame, so the WPF app shows
/// the image "coming in" (noise clearing / patches refining) rather than a whole frame at once.
///
/// Camera moves are LIVE - exactly like moonray_gui itself. We launch moonray_gui once with
/// <c>-deltas &lt;file&gt;</c> and leave it running; moonray_gui watches that file and, on change,
/// applies an in-process incremental <c>updateScene()</c> (a camera-only <c>node_xform</c> change
/// is not geometry, so it just re-renders from the new view - no scene reload, no relaunch).
/// The one caveat: moonray's ChangeWatcher is inotify-based and inotify does not observe
/// host-side writes on the /mnt/out bind mount, so the deltas file lives on a guest-local path
/// (/tmp) and a tiny bash "camera pump" mirrors host camera writes into it. Only a resolution
/// change (detail slider) still relaunches moonray_gui; the camera is preserved across it.
/// </summary>
public sealed class WslcRenderService : IDisposable
{
    private const string ImageName = "openmoonray:latest";
    private const string ImageRepo = "openmoonray";

    private readonly string _storagePath;
    private Session? _session;
    private bool _imageLoaded;

    // The WSL Container SDK projects WinRT objects that have COM apartment (thread) affinity:
    // an object created on one thread cannot be used from another ("interface marshalled for a
    // different thread" / RPC_E_WRONG_THREAD). We therefore own a single dedicated thread and
    // funnel EVERY SDK object call (session/container/process create, start, stop, dispose)
    // through it via <see cref="RunSdk"/>. Background loops here only touch the file system.
    private readonly BlockingCollection<Action> _sdkQueue = new();
    private readonly Thread _sdkThread;

    // Interactive session state.
    private Container? _container;
    private Microsoft.WSL.Containers.Process? _controller;
    private string? _outDir;
    private string _sceneInputs = "";
    private OrbitCamera? _camera;
    private readonly object _camLock = new();

    private CancellationTokenSource? _liveCts;
    private Task? _pollTask;
    private Task? _flushTask;

    // Resolution divisor (higher = coarser/faster). Camera moves keep this constant (they are
    // instant deltas), so we render at a single resolution and let progressive refinement do the
    // rest. Only the detail slider changes it (which does trigger a relaunch).
    private int _settleDivisor = 4;
    private volatile int _desiredRes = 4;

    // Camera changes just flag the current view dirty; the flush loop coalesces rapid orbit/pan
    // deltas into an atomic cam_delta.rdla write (moonray_gui picks it up live, no relaunch).
    private volatile bool _camDirty;

    // Resolution changes still restart moonray_gui, coalesced to at most one relaunch per window.
    private const int ReloadThrottleMs = 400;
    private volatile bool _reloadRequested;
    private long _lastReloadTicks;

    private readonly object _busyLock = new();
    private bool _busy;

    // Dedupe identical dumps (moonray_gui keeps the framebuffer after it converges).
    private long _lastFrameLen = -1;
    private DateTime _lastFrameWriteUtc = DateTime.MinValue;

    public event Action<string>? LogLine;
    public event Action<string>? Status;
    public event Action<LiveFrame>? FrameReady; // a freshly decoded live framebuffer
    public event Action<bool>? Busy;            // true while a (re)render is spinning up
    public event Action<bool>? OrbitAvailable;

    public WslcRenderService()
    {
        _storagePath = Path.Combine(AppContext.BaseDirectory, "WslcStorage", "moonray-viewer");
        _sdkThread = new Thread(SdkPump)
        {
            IsBackground = true,
            Name = "wslc-sdk",
        };
        _sdkThread.Start();
    }

    // ---- Single-threaded SDK dispatcher ----

    /// <summary>Consumes SDK work items one at a time on the dedicated <see cref="_sdkThread"/>.</summary>
    private void SdkPump()
    {
        foreach (var work in _sdkQueue.GetConsumingEnumerable())
        {
            try { work(); } catch { /* the item's own TCS captured the exception */ }
        }
    }

    /// <summary>Runs <paramref name="func"/> on the dedicated SDK thread and awaits its result.</summary>
    private Task<T> RunSdk<T>(Func<T> func)
    {
        var tcs = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        try
        {
            _sdkQueue.Add(() =>
            {
                try { tcs.SetResult(func()); }
                catch (Exception ex) { tcs.SetException(ex); }
            });
        }
        catch (Exception ex)
        {
            // Queue already completed (service disposed) - surface as a faulted task.
            tcs.TrySetException(ex);
        }
        return tcs.Task;
    }

    /// <summary>Runs <paramref name="action"/> on the dedicated SDK thread and awaits completion.</summary>
    private Task RunSdk(Action action) => RunSdk(() => { action(); return true; });

    public void SetInteractiveDivisor(int divisor)
    {
        _settleDivisor = Math.Clamp(divisor, 1, 16);
        _desiredRes = _settleDivisor;
        if (_container is not null)
        {
            RequestReload();
        }
    }

    /// <summary>Locate the openmoonray image tar produced by container\build-image.ps1.</summary>
    public static string? FindImageTar()
    {
        var env = Environment.GetEnvironmentVariable("MOONRAY_IMAGE_TAR");
        if (!string.IsNullOrEmpty(env) && File.Exists(env))
        {
            return env;
        }

        var local = Path.Combine(AppContext.BaseDirectory, "moonray.tar");
        if (File.Exists(local))
        {
            return local;
        }

        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (int i = 0; i < 8 && dir is not null; i++, dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "container", "moonray.tar");
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        return null;
    }

    /// <summary>Creates and starts the session and ensures the image is loaded (idempotent).</summary>
    public async Task EnsureReadyAsync(CancellationToken ct)
    {
        if (_session is null)
        {
            await RunSdk(() =>
            {
                try
                {
                    var v = WslcService.GetVersion();
                    LogLine?.Invoke($"WSL Container SDK version {v.Major}.{v.Minor}.{v.Revision}");
                }
                catch { /* version is informational only */ }

                Status?.Invoke("Creating WSL session...");
                var settings = new SessionSettings("MoonrayArrasViewer", _storagePath)
                {
                    CpuCount = (uint)Environment.ProcessorCount,
                    MemorySizeInMB = 8192,
                    VhdRequirements = new VhdOptions("moonray", 64UL * 1024 * 1024 * 1024, VhdType.Dynamic),
                };

                var session = new Session(settings);
                session.Start();
                _session = session;
                LogLine?.Invoke($"Session started ({Environment.ProcessorCount} CPUs, 8192 MB RAM).");
            });
        }

        if (!_imageLoaded)
        {
            await RunSdk(() =>
            {
                var session = _session!;

                // Check FIRST: is a good MoonRay image already in this session's store?
                // The per-session image store persists at _storagePath, so after the first
                // load later runs reuse it and skip the multi-minute import.
                Status?.Invoke("Checking for the MoonRay image...");
                if (HasGoodImage(session))
                {
                    _imageLoaded = true;
                    LogLine?.Invoke($"Found existing image '{ImageName}' in the session store - skipping load.");
                    return;
                }

                var tar = FindImageTar()
                    ?? throw new FileNotFoundException(
                        "moonray.tar not found. Build the image with container\\build-image.ps1 " +
                        "or set the MOONRAY_IMAGE_TAR environment variable.");

                Status?.Invoke("Loading image (one-time, can take several minutes)...");
                LogLine?.Invoke($"No cached image found. Loading '{ImageName}' from {tar} ...");
                session.LoadImage(tar);

                if (!HasGoodImage(session))
                {
                    throw new InvalidOperationException(
                        $"Image load finished but '{ImageName}' is not present in the session store.");
                }

                _imageLoaded = true;
                LogLine?.Invoke("Image loaded.");
            });
        }
    }

    /// <summary>True if a MoonRay image is already present in the session's image store.</summary>
    private static bool HasGoodImage(Session session)
    {
        foreach (var img in session.GetImages())
        {
            var name = img.Name ?? string.Empty;
            if (name.Equals(ImageName, StringComparison.OrdinalIgnoreCase) ||
                name.Equals(ImageRepo, StringComparison.OrdinalIgnoreCase) ||
                name.StartsWith(ImageRepo + ":", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }
        return false;
    }

    /// <summary>
    /// Begins an interactive session for <paramref name="sceneDir"/>: ensures the image is
    /// ready, parses the scene camera, spins up one long-lived container, and starts the live
    /// progressive render stream. Orbit / detail changes restart the stream from the new view.
    /// </summary>
    public async Task StartInteractiveAsync(string sceneDir, CancellationToken ct)
    {
        await EnsureReadyAsync(ct);
        await StopInteractiveAsync();

        var session = _session!;

        _camera = OrbitCamera.FromSceneDir(sceneDir);
        if (_camera is null)
        {
            LogLine?.Invoke("No camera found in the scene .rdla - orbit disabled (rendering the default view).");
        }
        else
        {
            LogLine?.Invoke($"Camera '{_camera.CameraName}' ({_camera.CameraType}) - drag on the image to orbit.");
        }
        OrbitAvailable?.Invoke(_camera is not null);

        _sceneInputs = BuildSceneInputs(sceneDir);

        _outDir = Path.Combine(Path.GetTempPath(), "MoonrayArrasViewer", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_outDir);
        // Seed the output dir BEFORE it is bind-mounted: virtiofs gives a freshly-created
        // *empty* host directory a stale inode (link count 0), so in-container writes fail
        // with ENOENT until a file exists at mount time.
        File.WriteAllText(Path.Combine(_outDir, ".wslc-keep"), "");

        // Initial control state the controller reads on its first launch.
        _desiredRes = _settleDivisor;
        WriteRes(_desiredRes);
        WriteCamDelta();
        // Drop the controller script into the (bind-mounted) out dir with LF endings so the
        // shebang works inside Linux regardless of how this file was checked out.
        File.WriteAllText(Path.Combine(_outDir, "live-render.sh"),
            LiveRenderScript.Replace("\r\n", "\n").Replace("\r", "\n"));

        await RunSdk(() =>
        {
            Status?.Invoke("Starting container...");
            var init = new ProcessSettings { CommandLine = new List<string> { "/bin/sleep", "86400" } };
            var cs = new ContainerSettings(ImageName)
            {
                Name = "wslc-moonray-live-" + Guid.NewGuid().ToString("N")[..8],
                InitProcess = init,
                EnableAutoRemove = true,
                Volumes = new List<ContainerVolume>
                {
                    new(sceneDir, "/mnt/scene", true),
                    new(_outDir, "/mnt/out", false),
                },
            };
            _container = session.CreateContainer(cs);
            _container.Start();
        });

        _lastFrameLen = -1;
        _lastFrameWriteUtc = DateTime.MinValue;
        _lastReloadTicks = Environment.TickCount64;
        _reloadRequested = false;
        _camDirty = false;

        SetBusy(true);
        Status?.Invoke("Starting live render...");

        await StartController();

        _liveCts = new CancellationTokenSource();
        _pollTask = Task.Run(() => PollLoop(_liveCts.Token));
        _flushTask = Task.Run(() => CameraFlushLoop(_liveCts.Token));
    }

    /// <summary>Launches the long-lived controller (moonray_gui + shmFb dump loop) in the container.</summary>
    private Task StartController()
    {
        return RunSdk(() =>
        {
            var container = _container!;
            var stdout = new StringBuilder();
            var proc = container.CreateProcess(new ProcessSettings
            {
                CommandLine = new List<string> { "/entrypoint.sh", "bash", "/mnt/out/live-render.sh" },
                OutputMode = ProcessOutputMode.Event,
            });
            proc.OutputReceived += data => LogLines(stdout, data);
            proc.ErrorReceived += data => LogLines(stdout, data);
            proc.Start();
            _controller = proc;
        });
    }

    /// <summary>Nudges the orbit camera and flags the view dirty for a live delta update.</summary>
    public void Orbit(double dAzimuthDeg, double dElevationDeg)
    {
        if (_camera is null)
        {
            return;
        }
        lock (_camLock)
        {
            _camera.AzimuthDeg += dAzimuthDeg;
            _camera.ElevationDeg = Math.Clamp(_camera.ElevationDeg + dElevationDeg, -85.0, 85.0);
        }
        _camDirty = true;   // flush loop writes cam_delta.rdla -> moonray_gui applies it live
    }

    /// <summary>
    /// Translate ("pan") the camera in its own screen plane - the WASD controls. Amounts are
    /// fractions of the orbit radius so movement speed stays consistent regardless of how far
    /// the camera is from the subject. Applied live via the deltas file (no relaunch).
    /// </summary>
    public void Pan(double rightFraction, double upFraction)
    {
        if (_camera is null)
        {
            return;
        }
        lock (_camLock)
        {
            double scale = _camera.Radius;
            _camera.Pan(rightFraction * scale, upFraction * scale);
        }
        _camDirty = true;
    }

    /// <summary>Called when a drag ends: make sure the final camera position is flushed live.</summary>
    public void Refine()
    {
        if (_container is null)
        {
            return;
        }
        _camDirty = true;
    }

    /// <summary>Restart the live stream at the current view (e.g. after the detail slider changes).</summary>
    public void Rerender()
    {
        if (_container is null)
        {
            return;
        }
        _desiredRes = _settleDivisor;
        RequestReload();
    }

    private void RequestReload() => _reloadRequested = true;

    // ---- Background loops ----

    /// <summary>
    /// Flushes coalesced camera changes to the deltas file (fast, no relaunch) and coalesces the
    /// far rarer resolution changes into at most one moonray_gui restart per throttle window.
    /// </summary>
    private async Task CameraFlushLoop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try { await Task.Delay(30, ct); }
            catch (OperationCanceledException) { break; }

            // Camera: coalesce a burst of orbit/pan deltas into one in-place write. moonray_gui's
            // -deltas watcher applies it in-process, so this is cheap and needs no restart.
            if (_camDirty)
            {
                _camDirty = false;
                WriteCamDelta();
            }

            // Resolution (detail slider): still a relaunch, throttled so slider drags don't thrash.
            if (_reloadRequested)
            {
                long now = Environment.TickCount64;
                if (now - _lastReloadTicks < ReloadThrottleMs)
                {
                    continue; // wait out the throttle window; keep the request pending
                }

                _reloadRequested = false;
                _lastReloadTicks = now;

                try
                {
                    WriteRes(_desiredRes);
                    File.WriteAllText(Path.Combine(_outDir!, "reload.flag"), "");
                    SetBusy(true);
                    Status?.Invoke($"Rendering (1/{_desiredRes} res)...");
                }
                catch { /* out dir racing on shutdown - ignore */ }
            }
        }
    }

    /// <summary>Watches live.ppm and raises FrameReady for each new/refined frame.</summary>
    private async Task PollLoop(CancellationToken ct)
    {
        string livePath = Path.Combine(_outDir!, "live.ppm");
        while (!ct.IsCancellationRequested)
        {
            try { await Task.Delay(50, ct); }
            catch (OperationCanceledException) { break; }

            try
            {
                var fi = new FileInfo(livePath);
                if (!fi.Exists || fi.Length < 16)
                {
                    continue;
                }

                // Skip if neither the size nor the write time changed since the last frame.
                if (fi.Length == _lastFrameLen && fi.LastWriteTimeUtc == _lastFrameWriteUtc)
                {
                    continue;
                }

                byte[]? bytes = TryReadAllBytes(livePath);
                if (bytes is null)
                {
                    continue; // locked mid-swap; try again next tick
                }

                var frame = PpmImage.TryParseP3(bytes);
                if (frame is null)
                {
                    continue; // truncated read raced the writer; try again next tick
                }

                _lastFrameLen = fi.Length;
                _lastFrameWriteUtc = fi.LastWriteTimeUtc;

                if (SetBusy(false))
                {
                    Status?.Invoke("Live - drag to orbit.");
                }
                FrameReady?.Invoke(frame);
            }
            catch (OperationCanceledException) { break; }
            catch { /* transient FS races - keep polling */ }
        }
    }

    private static byte[]? TryReadAllBytes(string path)
    {
        for (int attempt = 0; attempt < 3; attempt++)
        {
            try
            {
                using var fs = new FileStream(path, FileMode.Open, FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete);
                var buf = new byte[fs.Length];
                int read = 0;
                while (read < buf.Length)
                {
                    int n = fs.Read(buf, read, buf.Length - read);
                    if (n <= 0)
                    {
                        break;
                    }
                    read += n;
                }
                return read == buf.Length ? buf : null;
            }
            catch (IOException)
            {
                Thread.Sleep(30);
            }
        }
        return null;
    }

    /// <summary>Sets the busy state; returns true if it actually changed.</summary>
    private bool SetBusy(bool value)
    {
        lock (_busyLock)
        {
            if (_busy == value)
            {
                return false;
            }
            _busy = value;
        }
        Busy?.Invoke(value);
        return true;
    }

    private void WriteRes(int divisor)
    {
        if (_outDir is null)
        {
            return;
        }
        try { File.WriteAllText(Path.Combine(_outDir, "res.txt"), divisor.ToString()); }
        catch { /* ignore */ }
    }

    private void WriteCamDelta()
    {
        if (_outDir is null || _camera is null)
        {
            return;
        }
        string ovr;
        lock (_camLock)
        {
            ovr = _camera.BuildOverrideRdla();
        }
        try
        {
            // Write IN PLACE (stable inode) rather than via rename: on the /mnt/out bind mount a
            // rename-replace leaves the guest reading a stale dentry until its cache expires, but
            // an in-place overwrite is picked up promptly on the next stat. The controller's pump
            // guards against reading a half-written file via the trailing "--eof" sentinel.
            File.WriteAllText(Path.Combine(_outDir, "cam_delta.rdla"), ovr);
        }
        catch { /* ignore */ }
    }

    private static string BuildSceneInputs(string sceneDir)
    {
        var parts = new List<string>();
        if (File.Exists(Path.Combine(sceneDir, "scene.rdlb")))
        {
            parts.Add("-in scene.rdlb");
        }
        if (File.Exists(Path.Combine(sceneDir, "scene.rdla")))
        {
            parts.Add("-in scene.rdla");
        }
        if (parts.Count == 0)
        {
            foreach (var f in Directory.EnumerateFiles(sceneDir, "*.rdl*"))
            {
                parts.Add("-in " + Path.GetFileName(f));
            }
        }
        if (parts.Count == 0)
        {
            throw new FileNotFoundException("No scene.rdla/scene.rdlb found in " + sceneDir);
        }
        return string.Join(" ", parts);
    }

    private void LogLines(StringBuilder buffer, byte[] data)
    {
        buffer.Append(Encoding.UTF8.GetString(data));
        int newline;
        while ((newline = IndexOf(buffer, '\n')) >= 0)
        {
            string line = buffer.ToString(0, newline).TrimEnd('\r');
            buffer.Remove(0, newline + 1);
            if (line.Length > 0)
            {
                LogLine?.Invoke(line);
            }
        }
    }

    private static int IndexOf(StringBuilder sb, char c)
    {
        for (int i = 0; i < sb.Length; i++)
        {
            if (sb[i] == c)
            {
                return i;
            }
        }
        return -1;
    }

    /// <summary>Stops the live container, controller and loops (safe to call repeatedly).</summary>
    public async Task StopInteractiveAsync()
    {
        // NOTE: ConfigureAwait(false) throughout so this method never needs the WPF UI thread to
        // resume. Dispose() blocks the UI thread on this task (GetResult); without this a captured
        // SynchronizationContext continuation would deadlock (the UI thread is waiting on us).
        _liveCts?.Cancel();
        if (_pollTask is not null)
        {
            try { await _pollTask.ConfigureAwait(false); } catch { /* ignore */ }
            _pollTask = null;
        }
        if (_flushTask is not null)
        {
            try { await _flushTask.ConfigureAwait(false); } catch { /* ignore */ }
            _flushTask = null;
        }
        _liveCts?.Dispose();
        _liveCts = null;

        var ctl = _controller;
        _controller = null;
        var c = _container;
        _container = null;
        if (ctl is not null || c is not null)
        {
            try
            {
                await RunSdk(() =>
                {
                    if (ctl is not null)
                    {
                        try { ctl.Signal(Signal.SIGTERM); } catch { }
                        try { ctl.Dispose(); } catch { }
                    }
                    if (c is not null)
                    {
                        try { c.Stop(Signal.SIGTERM, TimeSpan.FromSeconds(5)); } catch { }
                        try { c.Dispose(); } catch { }
                    }
                }).ConfigureAwait(false);
            }
            catch { /* best effort teardown */ }
        }

        if (_outDir is not null)
        {
            try { Directory.Delete(_outDir, true); } catch { }
            _outDir = null;
        }
        _camera = null;
        lock (_busyLock) { _busy = false; }
    }

    public void Dispose()
    {
        try { StopInteractiveAsync().GetAwaiter().GetResult(); } catch { }

        var s = _session;
        _session = null;
        if (s is not null)
        {
            try
            {
                RunSdk(() =>
                {
                    try { s.Terminate(); } catch { }
                    try { s.Dispose(); } catch { }
                }).GetAwaiter().GetResult();
            }
            catch { }
        }

        _sdkQueue.CompleteAdding();
        try { _sdkThread.Join(TimeSpan.FromSeconds(5)); } catch { }
    }

    // Authoritative copy of container/live-render.sh, written to the out dir at runtime
    // (LF-normalized on write). Keep in sync with that file.
    private const string LiveRenderScript = """
#!/bin/bash
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

    READY=0
    for _ in $(seq 1 200); do
        sleep 0.1
        grep -q "threadMain() booted" "$OUT/gui.log" 2>/dev/null && { READY=1; break; }
        kill -0 "$GUI" 2>/dev/null || break
    done

    CTRL=0
    if [ "$READY" = 1 ]; then
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

    # Reclaim the killed instance's leaked System V shm segments so repeated resolution changes
    # don't exhaust shared memory or keep pushing the id higher.
    ipcs -m 2>/dev/null | awk '$2 ~ /^[0-9]+$/ {print $2}' | while read -r _id; do
        ipcrm -m "$_id" 2>/dev/null || true
    done
done
""";
}
