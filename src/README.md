# Source

- [`aks-desktop/`](aks-desktop/) — CPU Embedding Arena benchmark dashboard running on a local AKS Desktop cluster in WSL. See its [README](aks-desktop/README.md) for setup and run instructions.
- [`aks-desktop-gpu/`](aks-desktop-gpu/) — NVIDIA CUDA embedding benchmark on a local AKS Desktop cluster in WSL. See its [README](aks-desktop-gpu/README.md) for image build/import, NVIDIA runtime requirements, deployment, and GPU verification.
- [`gpu-fractal/`](gpu-fractal/) — CUDA Canvas fractal rendering in a GPU-enabled WSL container. See its [README](gpu-fractal/README.md) for build and run instructions.
- [`aspire-hot-takes/HotTakes/`](aspire-hot-takes/HotTakes/) — Aspire application with a backend API, web frontend, PostgreSQL, Redis, and an NPU agent running in WSL containers. See the [demo script](aspire-hot-takes/HotTakes/DEMO-SCRIPT.md); run `demo-prep.ps1` from the application folder to prepare the demo and download its model.
- [`moonray_gui/`](moonray_gui/) — Windows viewer for OpenMoonRay rendering inside a WSL container. See its [README](moonray_gui/README.md) for prerequisites and run instructions.

Build outputs, container images, downloaded models, and local caches are not included.
