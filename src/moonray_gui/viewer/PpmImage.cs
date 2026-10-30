namespace MoonrayArrasViewer;

/// <summary>A decoded live frame: BGRA32 pixels ready to blit into a WriteableBitmap.</summary>
public sealed class LiveFrame
{
    public required int Width { get; init; }
    public required int Height { get; init; }
    public required byte[] Bgra { get; init; } // Width*Height*4, BGRA order
}

/// <summary>
/// Minimal, fast reader for the ASCII PPM (P3) files that <c>shmFbDump</c> writes from
/// moonray_gui's live shared-memory framebuffer. The framebuffer is already tone-mapped
/// 8-bit RGB (what the GUI would display), so we only need to reorder to BGRA for WPF.
/// </summary>
internal static class PpmImage
{
    /// <summary>Parse a P3 PPM byte buffer into a <see cref="LiveFrame"/>, or null if invalid.</summary>
    public static LiveFrame? TryParseP3(byte[] data)
    {
        if (data.Length < 2 || data[0] != (byte)'P' || data[1] != (byte)'3')
        {
            return null;
        }

        int pos = 2;
        if (!TryReadInt(data, ref pos, out int width) ||
            !TryReadInt(data, ref pos, out int height) ||
            !TryReadInt(data, ref pos, out int maxVal) ||
            width <= 0 || height <= 0 || maxVal <= 0)
        {
            return null;
        }

        long pixelCount = (long)width * height;
        var bgra = new byte[pixelCount * 4];
        double scale = maxVal == 255 ? 1.0 : 255.0 / maxVal;

        int dst = 0;
        for (long i = 0; i < pixelCount; i++)
        {
            if (!TryReadInt(data, ref pos, out int r) ||
                !TryReadInt(data, ref pos, out int g) ||
                !TryReadInt(data, ref pos, out int b))
            {
                return null; // truncated (mid-write) - caller retries
            }

            bgra[dst++] = ToByte(b, scale);
            bgra[dst++] = ToByte(g, scale);
            bgra[dst++] = ToByte(r, scale);
            bgra[dst++] = 255;
        }

        return new LiveFrame { Width = width, Height = height, Bgra = bgra };
    }

    private static byte ToByte(int v, double scale)
    {
        if (scale != 1.0)
        {
            v = (int)(v * scale + 0.5);
        }
        return v < 0 ? (byte)0 : v > 255 ? (byte)255 : (byte)v;
    }

    /// <summary>Read the next whitespace-delimited non-negative integer, skipping PPM comments.</summary>
    private static bool TryReadInt(byte[] data, ref int pos, out int value)
    {
        value = 0;
        int n = data.Length;

        // Skip whitespace and '#' comment lines.
        while (pos < n)
        {
            byte c = data[pos];
            if (c == (byte)'#')
            {
                while (pos < n && data[pos] != (byte)'\n')
                {
                    pos++;
                }
            }
            else if (c == (byte)' ' || c == (byte)'\t' || c == (byte)'\r' || c == (byte)'\n')
            {
                pos++;
            }
            else
            {
                break;
            }
        }

        if (pos >= n || data[pos] < (byte)'0' || data[pos] > (byte)'9')
        {
            return false;
        }

        int v = 0;
        while (pos < n && data[pos] >= (byte)'0' && data[pos] <= (byte)'9')
        {
            v = v * 10 + (data[pos] - (byte)'0');
            pos++;
        }
        value = v;
        return true;
    }
}
