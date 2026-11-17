<p align="center">
<img src="img/banner-ignite-26.png" alt="Microsoft Ignite 2026" width="100%"/>
</p>

# [Microsoft Ignite 2026](https://ignite.microsoft.com)

## 🔥BRK395: Inside WSL containers for application development

### Session description

Understand WSL containers from architecture to deployment. Learn how to build and run Linux containers on Windows while exploring Windows integration, performance, security, management, and policy controls for enterprise-scale development.

### 🚀 Getting started

This repo contains the demos shown in the session. Each one runs Linux containers
on Windows with WSL containers (`wslc`), with no separate container engine
required.

1. Watch the session or review the [BRK395 session page](https://ignite.microsoft.com/en-US/sessions/BRK395).
2. Clone this repo:

   ```powershell
   git clone https://github.com/microsoft/ignite26-BRK395-inside-wsl-containers-for-application-development.git
   ```

3. Make sure WSL is up to date (`wsl --update`) and that `wslc` is available.
4. Pick a demo below and follow its README for prerequisites and run steps.

### 🧪 Demos

| Demo | What it shows | Hardware |
|------|---------------|----------|
| [Kernel Radio](src/kernel-radio/README.md) | A small web dashboard in a WSL container, reachable from Windows through port mapping | Any |
| [Hot Takes](src/aspire-hot-takes/HotTakes/README.md) | An Aspire app whose PostgreSQL, Redis, and AI agent containers run on WSL containers | Intel NPU optional (CPU fallback) |
| [CUDA Canvas](src/gpu-fractal/README.md) | A CUDA kernel in a Linux container rendering fractals for a Windows browser | NVIDIA GPU |
| [Moonray viewer](src/moonray_gui/README.md) | A Windows WPF app driving the Linux OpenMoonRay renderer inside a WSL container | Any |
| [Embedding Arena](src/aks-desktop/README.md) | A 14-model embedding benchmark on a local AKS Desktop Kubernetes cluster in WSL | Any |
| [Embedding Arena on GPU](src/aks-desktop-gpu/README.md) | The same benchmark with embeddings on CUDA | NVIDIA GPU |

See [`src/README.md`](src/README.md) for a summary of each folder.

### 🎯 Learning outcomes

- Explain how WSL containers work and how they integrate with Windows, including networking, GPU, and NPU access.
- Build and run Linux containers on Windows with `wslc`, from single containers to Aspire apps and local Kubernetes clusters.
- Identify the performance, security, management, and policy controls that matter for enterprise-scale development.

### 💻 Technologies used

- Windows Subsystem for Linux (WSL) and WSL containers (`wslc`)
- Aspire and .NET
- AKS Desktop and Kubernetes
- PostgreSQL with pgvector, and Redis
- Python, FastAPI, FastEmbed, and ONNX Runtime
- NVIDIA CUDA and Intel NPU with OpenVINO
- OpenMoonRay and WPF

### 📚 Continue your learning

Pick your next step based on your learning style:

| Resource | What you'll get |
|----------|-----------------|
| **[Windows Subsystem for Linux documentation](https://learn.microsoft.com/en-us/windows/wsl/)** | Microsoft Learn guidance for WSL installation, configuration, and use |
| **[WSL on GitHub](https://github.com/microsoft/WSL)** | Official WSL source repository, downloads, release notes, and contribution links |
| **[WSL open source developer documentation](https://wsl.dev/)** | Developer and architecture reference covering WSL architecture, security model, and development setup |
| **[Ignite 2026 Next Steps](https://aka.ms/ignite26-next-steps)** | Additional session repos and materials from Microsoft Ignite 2026 |
| **[Microsoft AI Skills Navigator](https://aka.ms/ignite26-asn)** | Discover AI skills, learning paths, and resources for your next step |
| **[Microsoft Learn](https://aka.ms/ignite26-learn)** | Official documentation and guided learning paths on these topics |
| **[Microsoft Foundry Community](https://aka.ms/MicrosoftFoundryDiscord-Ignite26)** | Connect with other learners and experts in our Discord community |

### 🌟 Microsoft Learn MCP Server

The Microsoft Learn MCP Server gives your AI agent direct access to Microsoft's official documentation — grounded, up-to-date answers about the topics in this session.

**GitHub Copilot CLI** — Install with:

```shell
copilot plugin install microsoftdocs/mcp
```

**VS Code** — One-click install:  
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Microsoft_Learn_MCP-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=microsoft-learn&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Flearn.microsoft.com%2Fapi%2Fmcp%22%7D)

For more information, visit the [Learn MCP Server repo](https://aka.ms/learnmcp).

### 👥 Content owners

<table>
<tr>
    <td align="center"><a href="https://github.com/craigloewen-msft">
        <img src="https://github.com/craigloewen-msft.png" width="100px;" alt="Craig Loewen"/><br />
        <sub><b>Craig Loewen</b></sub></a><br />
            <a href="https://github.com/craigloewen-msft" title="talk">📢</a>
    </td>
    <td align="center"><a href="https://github.com/ptrivedi">
        <img src="https://github.com/ptrivedi.png" width="100px;" alt="Pooja Trivedi"/><br />
        <sub><b>Pooja Trivedi</b></sub></a><br />
            <a href="https://github.com/ptrivedi" title="talk">📢</a>
    </td>
</tr></table>

### ⚖️ Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.

Any use of third-party trademarks or logos are subject to those third-party's policies.
