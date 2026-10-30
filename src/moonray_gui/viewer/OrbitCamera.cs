using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace MoonrayArrasViewer;

/// <summary>Minimal 3-component vector for the orbit-camera math.</summary>
internal readonly record struct Vec3(double X, double Y, double Z)
{
    public static Vec3 operator +(Vec3 a, Vec3 b) => new(a.X + b.X, a.Y + b.Y, a.Z + b.Z);
    public static Vec3 operator -(Vec3 a, Vec3 b) => new(a.X - b.X, a.Y - b.Y, a.Z - b.Z);
    public static Vec3 operator *(Vec3 a, double s) => new(a.X * s, a.Y * s, a.Z * s);
    public double Length => Math.Sqrt(X * X + Y * Y + Z * Z);
    public Vec3 Normalized { get { var l = Length; return l > 1e-12 ? new(X / l, Y / l, Z / l) : this; } }
    public static Vec3 Cross(Vec3 a, Vec3 b) =>
        new(a.Y * b.Z - a.Z * b.Y, a.Z * b.X - a.X * b.Z, a.X * b.Y - a.Y * b.X);
}

/// <summary>
/// An orbit ("turntable") camera derived from a MoonRay scene's camera. It reads the
/// camera's initial <c>node_xform</c>, picks a pivot roughly where the camera is looking,
/// and can regenerate a <c>node_xform</c> for any azimuth/elevation so the app can orbit
/// the view by writing a small override .rdla that MoonRay loads on top of the scene.
/// </summary>
internal sealed partial class OrbitCamera
{
    public string CameraType { get; }
    public string CameraName { get; }
    public Vec3 Pivot { get; private set; }
    public double Radius { get; }
    public double AzimuthDeg { get; set; }
    public double ElevationDeg { get; set; }
    public double InitialAzimuthDeg { get; }
    public double InitialElevationDeg { get; }

    private static readonly Vec3 WorldUp = new(0, 1, 0);

    private OrbitCamera(string type, string name, Vec3 pivot, double radius, double azim, double elev)
    {
        CameraType = type;
        CameraName = name;
        Pivot = pivot;
        Radius = radius;
        AzimuthDeg = InitialAzimuthDeg = azim;
        ElevationDeg = InitialElevationDeg = elev;
    }

    /// <summary>Parse the first camera declaration + node_xform from any .rdla in sceneDir.</summary>
    public static OrbitCamera? FromSceneDir(string sceneDir)
    {
        foreach (var file in Directory.EnumerateFiles(sceneDir, "*.rdla"))
        {
            string text;
            try { text = File.ReadAllText(file); }
            catch { continue; }

            var cam = CamRegex().Match(text);
            if (!cam.Success)
            {
                continue;
            }

            var xf = XformRegex().Match(text, cam.Index);
            if (!xf.Success)
            {
                continue;
            }

            var nums = xf.Groups["m"].Value
                .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .Select(s => double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var d) ? d : double.NaN)
                .ToArray();
            if (nums.Length < 16 || nums.Take(16).Any(double.IsNaN))
            {
                continue;
            }

            // node_xform is the camera's local->world transform, row-major. Row 3 is the
            // eye position; row 2 is the camera's local +Z axis (MoonRay cameras look
            // down local -Z).
            var eye = new Vec3(nums[12], nums[13], nums[14]);
            var zAxis = new Vec3(nums[8], nums[9], nums[10]).Normalized;
            var viewDir = zAxis * -1.0;

            // Anchor the orbit pivot roughly where the camera is looking. Using the
            // distance from the world origin keeps the subject (near origin in these
            // scenes) centered, and the initial angles reproduce the original view.
            double radius = Math.Max(0.05, eye.Length);
            var pivot = eye + viewDir * radius;

            var dir = (eye - pivot).Normalized; // == zAxis
            double elev = Math.Asin(Math.Clamp(dir.Y, -1.0, 1.0)) * 180.0 / Math.PI;
            double azim = Math.Atan2(dir.X, dir.Z) * 180.0 / Math.PI;

            return new OrbitCamera(cam.Groups["type"].Value, cam.Groups["name"].Value, pivot, radius, azim, elev);
        }

        return null;
    }

    private Vec3 EyeFromAngles()
    {
        double az = AzimuthDeg * Math.PI / 180.0;
        double el = ElevationDeg * Math.PI / 180.0;
        var dir = new Vec3(Math.Cos(el) * Math.Sin(az), Math.Sin(el), Math.Cos(el) * Math.Cos(az));
        return Pivot + dir * Radius;
    }

    /// <summary>Current camera basis (right/up/forward-away axes) for the present angles.</summary>
    private (Vec3 right, Vec3 up, Vec3 zAxis) Basis()
    {
        var eye = EyeFromAngles();
        var zAxis = (eye - Pivot).Normalized;              // local +Z (points away from target)
        var xAxis = Vec3.Cross(WorldUp, zAxis).Normalized; // local +X (right)
        if (xAxis.Length < 1e-6)
        {
            xAxis = new Vec3(1, 0, 0); // looking straight up/down: pick a stable right vector
        }
        var yAxis = Vec3.Cross(zAxis, xAxis);              // local +Y (up)
        return (xAxis, yAxis, zAxis);
    }

    /// <summary>
    /// Translate ("pan") the view in its own screen plane by shifting the orbit pivot along the
    /// camera's right/up axes. Because the eye is <c>Pivot + dir*Radius</c>, moving the pivot moves
    /// the eye by the same amount, giving a pure translation (WASD-style dolly across the frame).
    /// </summary>
    public void Pan(double rightAmount, double upAmount)
    {
        var (right, up, _) = Basis();
        Pivot += right * rightAmount + up * upAmount;
    }

    /// <summary>16 row-major floats of the camera local-&gt;world transform (MoonRay node_xform).</summary>
    public double[] NodeXform()
    {
        var eye = EyeFromAngles();
        var (xAxis, yAxis, zAxis) = Basis();

        return new double[]
        {
            xAxis.X, xAxis.Y, xAxis.Z, 0,
            yAxis.X, yAxis.Y, yAxis.Z, 0,
            zAxis.X, zAxis.Y, zAxis.Z, 0,
            eye.X,   eye.Y,   eye.Z,   1,
        };
    }

    /// <summary>A small .rdla that overrides just the camera transform for the current angles.</summary>
    public string BuildOverrideRdla()
    {
        var m = NodeXform();
        var sb = new StringBuilder();
        sb.Append(CameraType).Append("(\"").Append(CameraName).Append("\") {\n");
        sb.Append("    [\"node_xform\"] = Mat4(");
        for (int i = 0; i < 16; i++)
        {
            if (i > 0)
            {
                sb.Append(", ");
            }
            sb.Append(m[i].ToString("R", CultureInfo.InvariantCulture));
        }
        sb.Append("),\n}\n");
        // Completeness sentinel: the controller's camera pump only mirrors this file once it ends
        // with this marker, so an in-flight (torn) read of an in-place host write is never applied.
        sb.Append("--eof\n");
        return sb.ToString();
    }

    [GeneratedRegex(@"(?<type>[A-Za-z_]\w*Camera)\(""(?<name>[^""]+)""\)\s*\{", RegexOptions.Compiled)]
    private static partial Regex CamRegex();

    [GeneratedRegex(@"\[""node_xform""\]\s*=\s*(?:blur\(\s*)?Mat4\(\s*(?<m>[^)]*)\)", RegexOptions.Compiled)]
    private static partial Regex XformRegex();
}
