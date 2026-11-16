# aks-dev-ctl

Embedding Arena: a live, visual benchmark of 14 open-source embedding models running on **CPU** in a local single-node Kubernetes cluster created by `aks-dev-ctl.exe`. The vector database, a dataset seeder and one Job per model run in the cluster, and a web dashboard shows what Kubernetes is doing as the run progresses.

![flow](https://img.shields.io/badge/flow-START%20→%20DB%20→%20seed%20→%20benchmarks%20→%20leaderboard-7c5cff)

## Prepare (before the session)

Requirements: Windows with WSL, `kubectl` (`winget install Kubernetes.kubectl`), and the Microsoft-signed `aks-dev-ctl.exe` binary in `~/Downloads`.

From Windows PowerShell in this folder:

```powershell
.\scripts\setup.ps1
```

The script checks aks-desktop, kubectl and WSL networking, and caches the aks-desktop prerequisites. It then deploys the project once on a temporary cluster and deletes it, which caches the Python dependencies in the WSL distro so the live deploy is quick. Add `-SkipWarmup` to skip that last step. It doesn't leave a cluster running.

## Run the demo

Run these from Windows PowerShell in this folder.

### 1. Create the cluster

```powershell
$aks = "$HOME\Downloads\aks-dev-ctl.exe"
& $aks cluster create --name embedding-arena --kubernetes-version v1.36.1 --provider wsl
```

This takes about two minutes. aks-desktop provisions the `Ubuntu-24.04` WSL distro, brings up a single-node Kubernetes cluster and merges its credentials into `~\.kube\config`.

### 2. Apply the project to the cluster

```powershell
kubectl apply -k .
kubectl -n arena rollout status deploy/arena-dashboard
kubectl -n arena port-forward svc/arena-dashboard 8080:80
```

Open **http://localhost:8080** and press **START**. The port-forward runs in the foreground, so use a second terminal for step 3.

There is no image to build. `kubectl apply -k .` uses [`kustomization.yaml`](kustomization.yaml) to package the `app/` code as ConfigMaps, and every pod runs the stock `python:3.12-slim` image. On first start, the dashboard's `deps` init container installs `app/requirements.txt` into the node's cache.

### 3. Show that it's working

```powershell
& $aks cluster show --name embedding-arena    # aks-desktop's view: WSL distro, node IP, agent
kubectl cluster-info
kubectl get nodes -o wide                     # one Ready node, running in WSL
kubectl get pods -A                           # system pods plus the arena namespace
kubectl -n arena get all,configmaps
kubectl -n arena get pods -w                  # after START: postgres, the seed Job and the bench-* Jobs come and go
kubectl -n arena logs deploy/arena-dashboard -c deps
kubectl -n arena logs -l arena/role=worker --tail=5 --prefix
kubectl -n arena get events --sort-by=.lastTimestamp
```

### 4. Delete the cluster

aks-desktop has no stop command yet, so deleting is how you shut the cluster down.

```powershell
.\scripts\delete.ps1          # stop the port-forward and delete the cluster, keep the WSL distro and its caches
.\scripts\delete.ps1 -Purge   # also destroy the Ubuntu-24.04 distro
```

The script wraps `& $aks cluster delete --name embedding-arena --prune-kubeconfig`. After you change the app, rerun `kubectl apply -k .`, then run `kubectl -n arena rollout restart deploy/arena-dashboard`.

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
 Browser ──WebSocket──► arena-dashboard (FastAPI Deployment, via kubectl port-forward :8080)
                          │  Kubernetes API (ServiceAccount + Role in ns/arena)
                          ├─► Deployment/Service  arena-postgres  (pgvector/pgvector:pg17)
                          ├─► Job  seed-<run>     (downloads SciFact → docs/queries/qrels)
                          └─► Job  bench-<model>-<run>  ×N, at most max_parallel at once
                                   │  writes vectors / runs kNN queries
                                   └──► arena-postgres
                                   └──► POST /api/report (progress + metrics) → dashboard
```

- The orchestrator loop (`app/dashboard.py`) keeps `max_parallel` benchmark Jobs running, tracks pods and events, and pushes a full snapshot to every browser about twice a second.
- There is no custom image. Every pod runs `python:3.12-slim`, mounts the code from the `arena-app` ConfigMap, and imports its dependencies from `/var/lib/arena-cache/pylib` on the node. The dashboard's init container installs them there once, keyed by a hash of `requirements.txt`.
- Model weights, the dataset and the Python dependencies are all cached on the node at `/var/lib/arena-cache` (hostPath). That cache lives in the WSL distro and survives `cluster delete`, so later runs start quickly.

## Layout

```
app/                     dashboard.py · worker.py · seed.py · catalog.py · common.py · requirements.txt · static/ (UI)
k8s/arena.yaml           namespace, RBAC, dashboard Deployment (with the deps init container) + Service
kustomization.yaml       kubectl apply -k . entry point: arena.yaml plus the app code as ConfigMaps
scripts/setup.ps1        pre-session prep: checks, aks-desktop prereq download, dependency warm-up
scripts/delete.ps1       stops the port-forward and deletes the cluster (-Purge also removes the distro)
```

## Environment notes

- Everything runs in WSL. No Docker, podman or image registry is involved: containerd on the node pulls `python:3.12-slim` and `pgvector/pgvector:pg17` from Docker Hub.
- The cluster needs internet access to pull those images, the Python packages and the Hugging Face model weights. If `files.pythonhosted.org` is blocked (as on some corporate networks), the init container falls back to the internal PyPI feed proxy.
- The dashboard is exposed to Windows through a loopback-only `kubectl port-forward` on port 8080.
- aks-desktop needs WSL's default NAT networking. Remove any `networkingMode` line from `%USERPROFILE%\.wslconfig` and run `wsl --shutdown`. Other modes break the cluster: with `mirrored`, the node's own IP is routed out to Windows, so pods can't reach the API server; other modes block Windows from reaching the aks-desktop agent ("connection refused" during `cluster create`). If a cluster was created under another mode, run `.\scripts\delete.ps1 -Purge`, then create it again.

## Troubleshooting

```powershell
kubectl -n arena get pods,jobs
kubectl -n arena logs deploy/arena-dashboard -c deps    # dependency install
kubectl -n arena logs deploy/arena-dashboard
kubectl -n arena logs job/bench-<model>-<run>
```
