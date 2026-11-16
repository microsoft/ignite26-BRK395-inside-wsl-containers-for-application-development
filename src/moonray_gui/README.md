# Moonray-WSLc-Windows

An interactive Windows viewer that renders scenes with the Linux **OpenMoonRay** renderer live inside a WSL container.

## Prerequisites

- Windows with [WSL](https://learn.microsoft.com/windows/wsl/) and the WSL Container SDK (`wslc.exe`)
- [.NET 8 SDK or later](https://dotnet.microsoft.com/download) with the Windows Desktop runtime (the viewer can use a newer installed runtime).

## Run

1. Build the container image (one-time; builds OpenMoonRay from source, so it can take a while):

   ```powershell
   ./container/build-image.ps1
   ```

2. Launch the viewer:

   ```powershell
   dotnet run --project viewer
   ```

A sample scene is preloaded — press **Start**, then drag on the image to orbit the camera; the render refines live.

## Credits

- Rendering by [OpenMoonRay](https://openmoonray.org/) ([source](https://github.com/OpenMoonRay/openmoonray)), licensed under Apache-2.0.
- Sample "Coffee Maker" scene by cekuhnen, CC-BY — see [`samples/coffee_maker/LICENSE.txt`](samples/coffee_maker/LICENSE.txt).

## License

[MIT](LICENSE)
