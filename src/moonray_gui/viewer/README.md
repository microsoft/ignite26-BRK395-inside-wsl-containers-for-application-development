# viewer — MoonRay interactive viewer (C# / WPF)

A native Windows app that runs a MoonRay scene inside a WSL container and streams the
render **live** as it refines.

## Run

```powershell
dotnet run --project .
```

A sample scene is preloaded — press **Start**, then:

- **Drag** on the image to orbit the camera.
- **W / A / S / D** to slide the camera up / left / down / right.
- **Detail** slider trades speed for sharpness.

On first use the app performs a one-time load of `moonray.tar` (several minutes); later
runs skip it.
