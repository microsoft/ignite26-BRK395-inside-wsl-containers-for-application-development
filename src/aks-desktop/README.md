# aks-desktop

Embedding Arena on microk8s.

This is a live, visual benchmark of 14 open-source embedding models running on **CPU**. Everything runs in microk8s inside WSL Debian:

- the vector database
- a dataset seeder
- one container per model

A web dashboard shows what Kubernetes is doing as the run progresses.

![flow](https://img.shields.io/badge/flow-START%20→%20DB%20→%20seed%20→%20benchmarks%20→%20leaderboard-7c5cff)

## Quick start

From Windows PowerShell:

```powershell
.\scripts\setup-wsl.ps1
```

The script is idempotent. It:

- runs `wsl --install -d Debian`
- makes root and sudo passwordless
- enables systemd
- installs microk8s (with `dns` and `hostpath-storage`) and podman
- builds and deploys the arena
- opens **http://localhost:8080**

If the distro is already provisioned, redeploy after code changes from inside Debian:

```bash
bash /mnt/c/cDev/ignite26-brk395/src/aks-desktop/scripts/deploy.sh
```

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
- A single image, `arena:dev`, holds the dashboard, the seeder and the worker. It is built with podman and imported into microk8s' containerd (`scripts/build.sh`).
- Model weights and the dataset are cached on the node at `/var/lib/arena-cache` (hostPath), so later runs start instantly.

## Layout

```
app/                 dashboard.py · worker.py · seed.py · catalog.py · common.py · static/ (UI)
k8s/arena.yaml       namespace, RBAC, dashboard Deployment + Service
scripts/setup-wsl.ps1      Windows entry point (install + provision + deploy)
scripts/setup-debian.sh    root provisioning inside the distro
scripts/deploy.sh          build image → apply → rollout → port-forward unit
scripts/build.sh           podman build + import into microk8s
scripts/wsl-net-fix.*      WSL networking workarounds (systemd service + timer)
scripts/arena-portforward.service   exposes the dashboard on :8080 for Windows
```

## Environment notes and workarounds

- **WSL "Consomme" networking:** in this mode, every TCP connection to `127.0.0.0/8` inside the distro is routed through Windows. The relay mirrors kubelite's dual-stack `[::]:16443` listener as IPv6-only, so `kubectl` gets *connection refused* on `127.0.0.1:16443`. `wsl-net-fix.sh` adds narrowly scoped `ip rule`s that keep ports 16443 and 25000 on the Linux loopback.
  - The same script also whitelists Calico interfaces in Docker's `DOCKER-USER` chain. Docker sets the FORWARD policy to DROP, which breaks pod networking.
- **Dashboard access:** an explicit IPv4 listener (`kubectl port-forward --address 0.0.0.0 … 8080:80`) *is* mirrored to Windows, which is why `http://localhost:8080` works. The NodePort 30080 is also available on the distro's IP.
- **Corporate networks:** if `files.pythonhosted.org` is blocked, `build.sh` falls back to `https://packagefeedproxy.microsoft.io/pypi/simple/`. You can override this with `PIP_INDEX_URL=...`. Hugging Face (model weights) must be reachable from the pods.
- **podman builds** use `--network=host` because the default podman network inside WSL has no DNS.

## Troubleshooting

```bash
sudo microk8s kubectl -n arena get pods,jobs
sudo microk8s kubectl -n arena logs deploy/arena-dashboard
sudo microk8s kubectl -n arena logs job/bench-<model>-<run>
systemctl status arena-portforward wsl-net-fix.timer
```
