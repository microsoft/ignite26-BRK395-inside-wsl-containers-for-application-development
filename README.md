## Before you're done

This repo has been created for your Microsoft Ignite 2026 session. Here's how to get it ready.

**Easiest path — use the agent (recommended):**

- Open GitHub Copilot Chat and say `help me initialize repo`. The agent will walk you through getting the README populated.
- When you're ready to publish, say `help me finalize repo`. The agent will clean up unused folders, validate everything, and remove this "Before you're done" section and other extra stuff that attendees don't need to see.
- Curious how it works? Read the [agent workflow](.github/AGENT-WORKFLOW.md).

**Doing it manually?**

Fill in the sections below yourself, then:

- Delete any placeholder folders you don't need (`data/`, `infra/`, etc.)
- Delete this "Before you're done" section
- Delete `.github/agents/`, `.github/tests/`, `.github/copilot-instructions.md`, and `.github/AGENT-WORKFLOW.md` — these are template tooling, not part of your published repo

**Folder conventions:**

- Attendee step-by-step guidance goes in `instructions/`. If you use MkDocs or a docs site instead, put it in `docs/` and link to it from this README.
- Reference material and background reading go in `docs/`.
- You can add a `.devcontainer/` folder if needed.

---

<a name="start-building"></a>

<p align="center">
<img src="img/banner-ignite-26.png" alt="Microsoft Ignite 2026" width="100%"/>
</p>

# [Microsoft Ignite 2026](https://ignite.microsoft.com)

## 🔥BRK395: Inside WSL containers for application development

### Session description

Understand WSL containers from architecture to deployment. Learn how to build and run Linux containers on Windows while exploring Windows integration, performance, security, management, and policy controls for enterprise-scale development.

### 🚀 Getting started

#### In a guided session

For this breakout session:

1. Review the [official BRK395 session page](https://ignite.microsoft.com/en-US/sessions/BRK395).
2. Slides, recording, and demo resources have not yet been supplied.
3. Attendee step-by-step guidance has not yet been confirmed.
   [`instructions/`](instructions/README.md) currently contains only a template placeholder.

#### On your own

Post-event self-run steps have not yet been confirmed. The choice between
[`instructions/`](instructions/README.md) and a docs-site entry point is also
awaiting confirmation; neither currently provides session-specific guidance.

Demo source code is available in [`src/`](src/README.md): the CPU and GPU
Embedding Arenas, the GPU fractal and Kernel Radio containers, the Aspire HotTakes
application, and the Moonray Windows viewer.
The optional `data/`, `infra/`, and `docs/` folders remain placeholders.
Their use for this session has not yet been confirmed.

### 🎯 Learning outcomes

Draft outcomes based on the session description; awaiting content-owner confirmation:

- Explain WSL container architecture and Windows integration.
- Describe how to build and run Linux containers on Windows.
- Identify performance, security, management, and policy considerations for enterprise-scale development.

### 💻 Technologies used

- WSL
- Windows
- Linux containers

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

<!-- Remove this section if the Microsoft Learn MCP Server is not relevant to the session. -->

The Microsoft Learn MCP Server gives your AI agent direct access to Microsoft's official documentation — grounded, up-to-date answers about the topics in this session.

**GitHub Copilot CLI** — Install with:

```shell
copilot plugin install microsoftdocs/mcp
```

**VS Code** — One-click install:  
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Microsoft_Learn_MCP-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=microsoft-learn&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Flearn.microsoft.com%2Fapi%2Fmcp%22%7D)

For more information, visit the [Learn MCP Server repo](https://aka.ms/learnmcp).

### 👥 Content owners

Content owner names and GitHub handles have not yet been confirmed.
The owner table below remains an unfilled template.

<!-- TODO: Add yourself as a content owner
1. Change the src in the image tag to {your github url}.png
2. Change INSERT NAME HERE to your name
3. Change the github url in the final href to your url. -->

<table>
<tr>
    <td align="center"><a href="http://github.com/yourGitHubHandle">
        <img src="https://github.com/yourGitHubHandle.png" width="100px;" alt="INSERT NAME HERE"/><br />
        <sub><b>INSERT NAME HERE</b></sub></a><br />
            <a href="https://github.com/yourGitHubHandle" title="talk">📢</a>
    </td>
</tr></table>

### ⚖️ Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.

Any use of third-party trademarks or logos are subject to those third-party's policies.
