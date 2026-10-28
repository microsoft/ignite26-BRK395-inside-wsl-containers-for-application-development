using System.IO;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace MoonrayArrasViewer;

public partial class MainWindow : Window
{
    private const double OrbitSensitivity = 0.4; // degrees per pixel of drag
    private const double PanSpeedPerTick = 0.015; // fraction of the orbit radius per WASD tick

    private readonly WslcRenderService _service = new();
    private CancellationTokenSource? _cts;
    private bool _running;
    private bool _starting;
    private bool _orbitAvailable;

    private WriteableBitmap? _bitmap;

    private bool _dragging;
    private Point _lastPoint;
    private bool _movedWhileDragging;

    // Drives continuous WASD camera translation while keys are held.
    private readonly DispatcherTimer _moveTimer;

    public MainWindow()
    {
        InitializeComponent();

        _service.LogLine += line => Dispatcher.BeginInvoke(() => AppendLog(line));
        _service.Status += s => Dispatcher.BeginInvoke(() => StatusText.Text = s);
        _service.FrameReady += frame => Dispatcher.BeginInvoke(() => ShowFrame(frame));
        _service.Busy += busy => Dispatcher.BeginInvoke(() => SetBusy(busy));
        _service.OrbitAvailable += ok => Dispatcher.BeginInvoke(() =>
        {
            _orbitAvailable = ok;
            HintBadge.Visibility = ok ? Visibility.Visible : Visibility.Collapsed;
        });

        _moveTimer = new DispatcherTimer(DispatcherPriority.Input)
        {
            Interval = TimeSpan.FromMilliseconds(30),
        };
        _moveTimer.Tick += OnMoveTick;
        _moveTimer.Start();

        UpdateDetailText();

        Closed += (_, _) =>
        {
            _moveTimer.Stop();
            _cts?.Cancel();
            _service.Dispose();
        };

        var sample = FindDefaultScene();
        if (sample is not null)
        {
            SceneBox.Text = sample;
        }
    }

    private static string? FindDefaultScene()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (int i = 0; i < 8 && dir is not null; i++, dir = dir.Parent)
        {
            string candidate = Path.Combine(dir.FullName, "samples", "coffee_maker");
            if (Directory.Exists(candidate) &&
                (File.Exists(Path.Combine(candidate, "scene.rdla")) ||
                 File.Exists(Path.Combine(candidate, "scene.rdlb"))))
            {
                return candidate;
            }
        }
        return null;
    }

    private void OnBrowse(object sender, RoutedEventArgs e)
    {
        var dlg = new Microsoft.Win32.OpenFolderDialog
        {
            Title = "Select a MoonRay scene folder (contains scene.rdla / scene.rdlb)",
        };
        if (dlg.ShowDialog(this) == true)
        {
            SceneBox.Text = dlg.FolderName;
        }
    }

    private async void OnStartStop(object sender, RoutedEventArgs e)
    {
        if (_starting)
        {
            return;
        }

        if (_running)
        {
            await StopAsync();
            return;
        }

        string scene = SceneBox.Text.Trim();
        if (string.IsNullOrEmpty(scene) || !Directory.Exists(scene))
        {
            MessageBox.Show(this, "Please choose a valid scene folder first.",
                "MoonRay Arras Viewer", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        _starting = true;
        StartButton.IsEnabled = false;
        BrowseButton.IsEnabled = false;
        SceneBox.IsEnabled = false;
        Placeholder.Visibility = Visibility.Collapsed;
        _cts = new CancellationTokenSource();

        try
        {
            _service.SetInteractiveDivisor((int)DetailSlider.Value);
            await _service.StartInteractiveAsync(scene, _cts.Token);
            _running = true;
            StartButton.Content = "Stop";
        }
        catch (OperationCanceledException)
        {
            StatusText.Text = "Cancelled.";
        }
        catch (Exception ex)
        {
            AppendLog("ERROR: " + ex.Message);
            StatusText.Text = "Failed.";
            MessageBox.Show(this, ex.Message, "Failed to start",
                MessageBoxButton.OK, MessageBoxImage.Error);
            BrowseButton.IsEnabled = true;
            SceneBox.IsEnabled = true;
        }
        finally
        {
            _starting = false;
            StartButton.IsEnabled = true;
        }
    }

    private async Task StopAsync()
    {
        _cts?.Cancel();
        StartButton.IsEnabled = false;
        try
        {
            await _service.StopInteractiveAsync();
        }
        catch { /* best effort */ }

        _running = false;
        _orbitAvailable = false;
        HintBadge.Visibility = Visibility.Collapsed;
        StartButton.Content = "Start";
        StartButton.IsEnabled = true;
        BrowseButton.IsEnabled = true;
        SceneBox.IsEnabled = true;
        StatusText.Text = "Stopped.";
        SetBusy(false);
    }

    private void OnDetailChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        UpdateDetailText();
        if (_running)
        {
            _service.SetInteractiveDivisor((int)DetailSlider.Value);
            _service.Rerender();
        }
    }

    private void UpdateDetailText()
    {
        if (DetailText is not null && DetailSlider is not null)
        {
            DetailText.Text = $"1/{(int)DetailSlider.Value} resolution";
        }
    }

    // ---- WASD camera translation ----

    private void OnMoveTick(object? sender, EventArgs e)
    {
        if (!_running || !_orbitAvailable || !IsActive)
        {
            return;
        }

        // A/D translate the camera left/right, W/S up/down (screen-space pan). Held keys move
        // continuously; each step is a live camera delta, so the render follows immediately.
        double right = 0, up = 0;
        if (Keyboard.IsKeyDown(Key.A)) right -= 1;
        if (Keyboard.IsKeyDown(Key.D)) right += 1;
        if (Keyboard.IsKeyDown(Key.W)) up += 1;
        if (Keyboard.IsKeyDown(Key.S)) up -= 1;

        if (right == 0 && up == 0)
        {
            return;
        }

        _service.Pan(right * PanSpeedPerTick, up * PanSpeedPerTick);
    }

    // ---- Orbit drag ----

    private void OnViewportMouseDown(object sender, MouseButtonEventArgs e)
    {
        if (!_running || !_orbitAvailable)
        {
            return;
        }
        _dragging = true;
        _movedWhileDragging = false;
        _lastPoint = e.GetPosition(ViewportBorder);
        ViewportBorder.CaptureMouse();
    }

    private void OnViewportMouseMove(object sender, MouseEventArgs e)
    {
        if (!_dragging)
        {
            return;
        }
        var p = e.GetPosition(ViewportBorder);
        double dx = p.X - _lastPoint.X;
        double dy = p.Y - _lastPoint.Y;
        _lastPoint = p;

        if (dx == 0 && dy == 0)
        {
            return;
        }
        _movedWhileDragging = true;

        // Drag right -> orbit right; drag up -> raise the camera.
        _service.Orbit(dx * OrbitSensitivity, -dy * OrbitSensitivity);
    }

    private void OnViewportMouseUp(object sender, MouseEventArgs e)
    {
        if (!_dragging)
        {
            return;
        }
        _dragging = false;
        ViewportBorder.ReleaseMouseCapture();

        // When the drag ends, render one sharper still of the final view.
        if (_movedWhileDragging)
        {
            _service.Refine();
        }
    }

    // ---- Frame display ----

    private void ShowFrame(LiveFrame frame)
    {
        // The service delivers already-decoded BGRA pixels from moonray_gui's live
        // framebuffer. Blit them into a reused WriteableBitmap so successive refinements
        // update in place (no per-frame allocation) - the image visibly clears up.
        if (_bitmap is null ||
            _bitmap.PixelWidth != frame.Width ||
            _bitmap.PixelHeight != frame.Height)
        {
            _bitmap = new WriteableBitmap(frame.Width, frame.Height, 96, 96, PixelFormats.Bgra32, null);
            FrameImage.Source = _bitmap;
        }

        _bitmap.WritePixels(
            new Int32Rect(0, 0, frame.Width, frame.Height),
            frame.Bgra, frame.Width * 4, 0);
        Placeholder.Visibility = Visibility.Collapsed;
    }

    private void SetBusy(bool busy)
    {
        BusyBar.IsIndeterminate = busy;
        BusyDot.Foreground = busy
            ? System.Windows.Media.Brushes.DeepSkyBlue
            : new System.Windows.Media.SolidColorBrush(
                System.Windows.Media.Color.FromRgb(0x55, 0x55, 0x55));
    }

    private void AppendLog(string line)
    {
        LogBox.AppendText(line + Environment.NewLine);
        LogBox.ScrollToEnd();
    }
}
