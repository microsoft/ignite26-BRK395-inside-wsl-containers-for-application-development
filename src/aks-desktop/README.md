# Embedding Arena on AKS Desktop

This is a live, visual benchmark of 14 open-source embedding models running on **CPU**. Everything runs on an existing local AKS Desktop cluster inside WSL:

- the vector database
- a dataset seeder
- one container per model

A web dashboard shows what Kubernetes is doing as the run progresses.

![flow](https://img.shields.io/badge/flow-START%20→%20DB%20→%20seed%20→%20benchmarks%20→%20leaderboard-7c5cff)

## Prerequisites

- An existing local cluster created by `aks-desktop.exe`, with a Ready node and
  working cluster DNS. This guide does not install or provision a cluster.
- Windows `kubectl` and the cluster's kubeconfig.
- An existing `wslc` installation for building and saving Linux container images.
- Network access approved by your organization for the Python base image,
  Python dependencies, Postgres image, dataset, and model downloads.
- Cluster policies allowing the workers' cache at `/var/lib/arena-cache`
  (`hostPath`).

No GPU or NVIDIA runtime is required. The CPU Arena uses namespace `arena` and
port `8080`; [the GPU Arena](../aks-desktop-gpu/README.md) uses `arena-gpu` and
port `8081`.

## Select the cluster

From the repository root, run in **Windows PowerShell**. Replace the kubeconfig
placeholder and set `$aksDistro` to the WSL distro used by your AKS Desktop
cluster; `Ubuntu-24.04` is the example used in the GPU setup guide.

```powershell
Set-Location .\src\aks-desktop
$env:KUBECONFIG = "C:\path\to\your\kubeconfig"
$aksDistro = "Ubuntu-24.04"
$image = "docker.io/library/arena:dev"
$archive = Join-Path $HOME "Downloads\arena-cpu.tar"

if (!(Test-Path -LiteralPath $env:KUBECONFIG)) { throw "Kubeconfig not found: $env:KUBECONFIG" }

kubectl config current-context
kubectl get nodes -o wide
wsl --list --verbose
```

Confirm that the context and distro belong to the intended local cluster and
that the node is **Ready**, with enough free CPU and memory. `KUBECONFIG` is set
only for this terminal; repeat the assignment in each new terminal.

## Build and save the image

Continue in the same terminal. `wslc` defaults to `linux/amd64`.
Use the default Python index, or set an IT-approved package feed:

```powershell
$pipIndexUrl = ""
# Only if approved by IT:
# $pipIndexUrl = "https://packagefeedproxy.microsoft.io/pypi/simple/"

wslc build --build-arg "PIP_INDEX_URL=$pipIndexUrl" -t $image .\app
if ($LASTEXITCODE -ne 0) { throw "Image build failed; do not proceed to save or import." }

wslc save --output $archive $image
if ($LASTEXITCODE -ne 0) { throw "Image save failed." }
if (!(Test-Path -LiteralPath $archive)) { throw "Image archive not found: $archive" }
```

## Import into the cluster

Import into the cluster's Kubernetes image store, not a separate Docker or
Podman image store. These commands follow the containerd default socket and
`k8s.io` namespace used by the GPU setup guide. If your installation differs,
use its Kubernetes runtime endpoint; do not install another containerd.

```powershell
$linuxArchive = wsl -d $aksDistro --exec wslpath -u "$archive"
if ($LASTEXITCODE -ne 0 -or !$linuxArchive) { throw "Could not convert the archive path for WSL." }
$linuxArchive = $linuxArchive.Trim()

wsl -d $aksDistro --exec test -f "$linuxArchive"
if ($LASTEXITCODE -ne 0) { throw "Archive is not visible inside the cluster distro: $linuxArchive" }

wsl -d $aksDistro -u root --exec ctr --namespace k8s.io images import "$linuxArchive"
if ($LASTEXITCODE -ne 0) { throw "Image import failed." }

$images = @(wsl -d $aksDistro -u root --exec ctr --namespace k8s.io images list --quiet)
if ($LASTEXITCODE -ne 0 -or $image -notin $images) { throw "Expected image is missing from the cluster: $image" }
$images | Where-Object { $_ -eq $image }
```

The exact tag must be `docker.io/library/arena:dev`, matching both `image` and
`ARENA_IMAGE` in `k8s\arena.yaml`. The manifest uses `IfNotPresent`.
For a multi-node cluster, import into each eligible node's Kubernetes image
store or use an approved registry accessible to every node.

## Deploy and open the dashboard

```powershell
kubectl apply -f .\k8s\arena.yaml
if ($LASTEXITCODE -ne 0) { throw "Deployment failed." }

kubectl -n arena rollout status deployment/arena-dashboard --timeout=180s
if ($LASTEXITCODE -ne 0) { throw "Dashboard did not become ready; inspect its pod and events." }

kubectl -n arena get pods
kubectl -n arena port-forward service/arena-dashboard 8080:80
```

Keep the terminal open and browse to **http://localhost:8080**.

After code changes, repeat the build, save, and import steps, then restart the
dashboard so it uses the newly imported image:

```powershell
kubectl -n arena rollout restart deployment/arena-dashboard
if ($LASTEXITCODE -ne 0) { throw "Dashboard restart failed." }
kubectl -n arena rollout status deployment/arena-dashboard --timeout=180s
if ($LASTEXITCODE -ne 0) { throw "Dashboard did not become ready; inspect its pod and events." }
```

Re-establish port-forwarding if the dashboard pod was replaced. Stop a running
benchmark before rebuilding; restart it afterward to launch new workers with
the updated image.

## Using the dashboard

| Control | What it does |
|---|---|
| **START** | Creates the pgvector database, seeds the dataset, then launches benchmark Jobs |
| **STOP** | Deletes the running benchmark Jobs and keeps the DB and the results so far. START then reads **RESUME** and continues the queue |
| **RESET** | Tears everything down, including the DB, and clears the leaderboard |
| **⚙ settings** (top right) | Models in parallel (default **2**, applies immediately); CPU threads per model (default 4, used by models launched after the change); corpus size (default 500, applies on the next seed); leaderboard ranking metric |

The page has four areas:

- **Cluster stage:** the database sits in the centre.
  - Each model pod appears as a card with a progress bar. The phases are connecting, loading, embedding, indexing, querying and completed.
  - Each card is wired to the DB. Packets flow toward the DB while documents are written, and back while queries run.
  - The DB panel shows live Postgres stats from `pg_stat_*`.
- **Leaderboard:** each finished model slides into its ranked slot.
- **`kubectl get pods`:** shows the live pod list.
- **Event stream:** shows Kubernetes events plus progress reports from the workers.

## What is measured

The dataset is [BEIR SciFact](https://github.com/beir-cellar/beir): 300 test queries with relevance judgements. The corpus is every relevant document plus random distractors, up to the configured corpus size. Documents are truncated to 600 characters so a full run takes minutes rather than hours on CPU.

Each worker (one Kubernetes Job per model) does the following:

1. Loads the model with [fastembed](https://github.com/qdrant/fastembed) (ONNX Runtime, CPU only).
2. Embeds the corpus and streams the vectors into its own `emb_<model>` pgvector table.
3. Builds an HNSW cosine index.
4. Embeds each query and retrieves the top 10 results from Postgres.

It reports **nDCG@10**, **Recall@10**, **MRR@10**, embedding throughput (docs/s), query-embedding latency, search latency (p50 and p95), memory use and index size.

The models are BGE Small, Base and Large v1.5; all-MiniLM-L6-v2; Multilingual MiniLM L12 and MPNet Base; Snowflake Arctic Embed XS, S and M; Jina v2 Small and Base; GTE Base; Nomic Embed v1.5; and mxbai Embed Large. They are queued roughly smallest-first and are defined in `app/catalog.py`.

## Architecture

```
 Browser ──WebSocket──► arena-dashboard (FastAPI, Deployment + NodePort 30080)
                          │  Kubernetes API (ServiceAccount + Role in ns/arena)
                          ├─► Deployment/Service  arena-postgres  (pgvector/pgvector:pg17)
                          ├─► Job  seed-<run>     (downloads SciFact → docs/queries/qrels)
                          └─► Job  bench-<model>-<run>  ×N, at most max_parallel at once
                                   │  writes vectors / runs kNN queries
                                   └──► arena-postgres
                                   └──► POST /api/report (progress + metrics) → dashboard
```

- The orchestrator loop (`app/dashboard.py`) keeps `max_parallel` benchmark Jobs running, tracks pods and events, and pushes a full snapshot to every browser about twice a second.
- A single image, `docker.io/library/arena:dev`, holds the dashboard, the seeder and the worker. It is built with `wslc` and imported into the AKS Desktop cluster's containerd image store.
- Model weights and the dataset are cached on the node at `/var/lib/arena-cache` (hostPath), so later runs start instantly.

## Layout

```
app/                 dashboard.py · worker.py · seed.py · catalog.py · common.py · static/ (UI)
k8s/arena.yaml       namespace, RBAC, dashboard Deployment + Service
```

## Environment notes

- **Dashboard access:** Windows `kubectl port-forward` provides local access
  without installing a Linux service or changing WSL networking rules.
- **Corporate networks:** if downloads are blocked, obtain an approved feed or
  request access from IT. Build-time Python feed settings do not configure
  runtime dataset or model downloads. Hugging Face must be reachable from the
  pods. Do not disable TLS verification or endpoint protection.
- **Local images:** `docker.io/library/arena:dev` is a local demo tag, not a
  published image. A Docker Hub "access denied" error means the image is
  missing from the cluster's image store or named differently.

## Troubleshooting

```powershell
kubectl config current-context
kubectl -n arena get pods,jobs
kubectl -n arena get events --sort-by=.lastTimestamp
kubectl -n arena logs deployment/arena-dashboard
# Replace the placeholder with a Job name from the list above.
kubectl -n arena logs job/<job-name>
```

If `kubectl` tries `http://localhost:8080/api`, set `KUBECONFIG` to the intended
cluster's kubeconfig; that address is not the Arena's Kubernetes API. For
pending workers, inspect their pod events and available CPU/memory, and lower
**Models in parallel** if necessary.
