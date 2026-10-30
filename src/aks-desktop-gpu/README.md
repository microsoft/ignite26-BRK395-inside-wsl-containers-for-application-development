# Embedding Arena on NVIDIA GPU

This is a separate GPU version of [the CPU Arena](../aks-desktop/README.md).
It runs the same 14-model BEIR SciFact benchmark on a local AKS Desktop cluster
inside WSL. The original CPU code is unchanged.

Document and query embeddings use FastEmbed with ONNX Runtime's CUDA execution
provider. Postgres, HNSW indexing, vector searches, dataset seeding, and the
dashboard itself remain CPU workloads. The browser can still render the
dashboard using an Intel GPU; that does not select the embedding device.

## What changed

- The image includes CUDA 12.8 and cuDNN 9, with Python 3.12 in a virtual environment.
- Dependencies use `fastembed-gpu==0.9.0` and `onnxruntime-gpu==1.23.2`, not their CPU variants.
- Workers explicitly select CUDA, check the loaded session's providers, and fail
  if CUDA initialization falls back to a CPU-only session.
- Only benchmark workers reserve `nvidia.com/gpu: 1`; the seeder and dashboard do not.
- Benchmark workers select `runtimeClassName: nvidia` so AKS Desktop injects the
  WSL NVIDIA driver libraries and devices; GPU allocation alone is not sufficient.
- The default and maximum concurrency are one model, for a single-GPU node.
- Resources use the separate `arena-gpu` namespace. The CPU Arena in `arena` is
  not modified or cleaned up by this dashboard.

CUDA-enabled sessions can still execute some unsupported or housekeeping
operations on CPU. The provider check prevents an entirely CPU-only session;
it does not claim that every operation runs on the GPU.

## Prerequisites

- An existing local AKS Desktop cluster with its NVIDIA device plugin working.
  `kubectl describe node` should list `nvidia.com/gpu: 1` under allocatable resources.
- A `nvidia` RuntimeClass backed by NVIDIA Container Runtime
  (`kubectl get runtimeclass nvidia`). This version selects it for workers only.
- A Windows NVIDIA driver compatible with CUDA 12.8 and WSL GPU support.
  Use the Windows driver; do not install a Linux NVIDIA display driver in WSL.
- An existing Docker or Podman installation capable of building Linux/amd64 images.
- Windows `kubectl` and your cluster's kubeconfig.
- Network access approved by your organization for the CUDA image, Ubuntu
  packages, Python dependencies, Postgres image, dataset, and model downloads.
- Cluster policies allowing the worker cache's `hostPath` volumes.

This is the local cluster created by `aks-desktop.exe`, not an Azure-hosted AKS
cluster accessed from WSL. The commands below assume its kubeconfig is saved as
`config` in your Windows Downloads folder and its distro is `Ubuntu-24.04`.
Set `$aksDistro` and `$env:KUBECONFIG` to your actual values.
Do not run the original microk8s provisioning, build, or deploy scripts, and do
not assume an image in Docker or Podman is already available to Kubernetes.

## Select the cluster

From the repository root, run in **Windows PowerShell**:

```powershell
Set-Location .\src\aks-desktop-gpu
$env:KUBECONFIG = Join-Path $HOME "Downloads\config"
$aksDistro = "Ubuntu-24.04"
$image = "docker.io/library/arena:gpu-v2"
$archive = Join-Path $HOME "Downloads\arena-gpu-v2.tar"

if (!(Test-Path -LiteralPath $env:KUBECONFIG)) { throw "Kubeconfig not found: $env:KUBECONFIG" }

kubectl config current-context
kubectl get nodes -o wide
kubectl describe nodes
kubectl get runtimeclass nvidia
wsl --list --verbose
wsl -d $aksDistro --exec /usr/lib/wsl/lib/nvidia-smi
```

Confirm the intended node is **Ready**, advertises an allocatable
`nvidia.com/gpu`, and has enough free CPU/memory. Both the `nvidia` RuntimeClass
and the WSL NVIDIA driver must be available before continuing. This application
does not install or configure them.

`KUBECONFIG` is set only for this PowerShell session. Repeat that assignment in
each new terminal. If `kubectl` tries `http://localhost:8080/api`, it is not using
the intended kubeconfig; that address is not the Arena's Kubernetes API.

## Build and save the image

Continue in the same terminal. Choose **one** builder:

```powershell
$builder = "docker"
# Or, if you use Podman:
# $builder = "podman"
```

Use the default Python index, or set an IT-approved package feed. If your
organization blocks `files.pythonhosted.org`, obtain an approved feed before
building; the commented Microsoft proxy is not an instruction to bypass policy.

```powershell
$pipIndexUrl = ""
# Only if approved by IT:
# $pipIndexUrl = "https://packagefeedproxy.microsoft.io/pypi/simple/"

& $builder build --platform linux/amd64 --build-arg "PIP_INDEX_URL=$pipIndexUrl" -t $image .\app
if ($LASTEXITCODE -ne 0) { throw "Image build failed; do not proceed to save or import." }

if ($builder -eq "podman") {
    & $builder save --format docker-archive --output $archive $image
} else {
    & $builder save --output $archive $image
}
if ($LASTEXITCODE -ne 0) { throw "Image save failed." }
if (!(Test-Path -LiteralPath $archive)) { throw "Image archive not found: $archive" }
```

The CUDA image is significantly larger than the CPU image. If corporate
security blocks a download, use an approved feed or request access from IT.
Do not disable TLS verification or endpoint protection.

## Import into the cluster

Continue in the same terminal. `--exec` bypasses the Linux shell, which otherwise
can strip backslashes from a Windows path passed to `wslpath`. Check the command
result before calling `.Trim()`:

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

The exact tag must be `docker.io/library/arena:gpu-v2`, matching both `image` and
`ARENA_IMAGE` in `k8s\arena.yaml`. The manifest uses `IfNotPresent`, so it will use
this imported image. `docker.io/library/arena` is not a published demo repository;
a pull from Docker Hub failing with "access denied" means the local image is
missing or named differently, not that you need Docker Hub credentials.

These import commands use containerd's default socket and `k8s.io` namespace,
as verified on the AKS Desktop setup used for this demo. If your installation
differs, use its Kubernetes runtime endpoint; do not install another containerd.
For a multi-node cluster, import into each eligible node's Kubernetes image store
or use an approved registry accessible to every node.

## Deploy and open the dashboard

```powershell
kubectl apply -f .\k8s\arena.yaml
if ($LASTEXITCODE -ne 0) { throw "Deployment failed." }

kubectl -n arena-gpu rollout status deployment/arena-dashboard --timeout=180s
if ($LASTEXITCODE -ne 0) { throw "Dashboard did not become ready; inspect its pod and events." }

kubectl -n arena-gpu get pods
kubectl -n arena-gpu port-forward service/arena-dashboard 8081:80
```

Keep the terminal open and browse to **http://localhost:8081**. This leaves port
8080 available for the CPU dashboard.

Select **START**. The dashboard creates Postgres, seeds the dataset, then runs
one GPU benchmark Job at a time. Only pods labeled `arena/role=worker` request
the GPU and select `runtimeClassName: nvidia`. The namespace also contains
CPU-based dashboard, seeder, and database pods.

The default corpus is 500 documents, and documents are truncated to 600
characters. To run a larger workload:

1. Open **Settings** using the gear button in the top-right.
2. Set **Corpus size (docs)** to **5000** and wait a second for it to save.
3. Select **RESET**, then **START**. RESET deletes existing results.

The next seed includes up to 5000 documents. Settings survive RESET but not a
dashboard restart. CPU threads per model still control CPU-side processing;
they do not choose a GPU. Models run serially because each reserves one GPU.

## Verify GPU execution

In another Windows PowerShell terminal, start monitoring before the benchmark:

```powershell
$env:KUBECONFIG = Join-Path $HOME "Downloads\config"
wsl -d Ubuntu-24.04 --exec /usr/lib/wsl/lib/nvidia-smi -l 1
```

Press **Ctrl+C** to stop monitoring. This works across short-lived workers;
Windows Task Manager's default **3D** graph is not a reliable CUDA workload view.
Select a CUDA/Compute graph if available.

Inspect the latest benchmark worker, including one that has already completed:

```powershell
kubectl -n arena-gpu get pods,jobs

$workers = @(kubectl -n arena-gpu get pods -l arena/role=worker `
    --sort-by=.metadata.creationTimestamp -o name)
if ($LASTEXITCODE -ne 0 -or $workers.Count -eq 0) { throw "No worker pods found; wait for seeding, or START a new run." }
$worker = $workers[-1]

kubectl -n arena-gpu describe $worker
kubectl -n arena-gpu logs $worker --tail=100
```

The pod description should show **Runtime Class: nvidia** and GPU limits/requests
of one. After model loading, successful logs include:

```text
ONNX Runtime active providers: ['CUDAExecutionProvider', 'CPUExecutionProvider']
```

The dashboard event stream reports `CUDA model ready`; completed metrics include
`execution_provider: CUDAExecutionProvider`. CPU alongside CUDA is normal and
does not mean the session fell back to CPU-only execution.

You can run `kubectl -n arena-gpu exec $worker -- nvidia-smi` while that pod is
running. A **Succeeded** pod cannot accept `exec`; read its logs instead. RESET,
STOP, dashboard startup cleanup, and the one-hour Job cleanup can remove workers,
so reacquire the pod name rather than reusing a stale `$worker`.

GPU utilization can be intermittent with small batches and while Postgres is
working: batches contain 16 documents, then the worker writes vectors to the
database. Model download/loading, indexing, and database queries do not keep the
GPU continuously busy. `rss_mb` measures host-process memory, not GPU VRAM. A
small corpus or CPU-oriented/quantized model is not guaranteed to be faster than
the CPU version.

## Update an existing deployment

For code changes, use a fresh tag such as `gpu-v3` to avoid cached images.
Change `$image` and `$archive`, then repeat the build, save, and import steps.
Update **both** `image` and `ARENA_IMAGE` in `k8s\arena.yaml` to the new tag, then
apply that manifest once. This updates the dashboard and its worker-image
configuration together, rather than starting two separate rollouts:

```powershell
kubectl apply -f .\k8s\arena.yaml
if ($LASTEXITCODE -ne 0) { throw "Deployment update failed." }
kubectl -n arena-gpu rollout status deployment/arena-dashboard --timeout=180s
if ($LASTEXITCODE -ne 0) { throw "Rollout did not finish; inspect the deployment and pods." }
```

The Deployment uses **Recreate**, so a short dashboard outage is expected.
Restart the port-forward if it disconnected, refresh the browser, and select
START again. Restarting the GPU dashboard intentionally deletes its own Jobs
and database; results are not persistent.

## Troubleshooting

```powershell
kubectl -n arena-gpu get events --sort-by=.metadata.creationTimestamp
kubectl -n arena-gpu logs deployment/arena-dashboard --tail=100
kubectl describe nodes
```

- **Build cannot find fastembed-gpu:** use the pinned `0.9.0` version available
  from the documented proxy, not `0.9.1`. CPU and GPU package releases need not
  be available together.
- **Feed returns 404 for a `.whl.metadata` URL:** the proxy returned this
  temporarily during setup, and the same URL subsequently served the metadata.
  Retry the build once. If it persists, report the exact URL to the feed owner
  or IT rather than switching resolvers, disabling TLS, or repeatedly changing
  package versions. A trailing WSL `E_FAIL` alone does not establish a WSL fault.
- **Archive not found:** do not continue after a failed build or save. Check the
  Windows `$archive` path before importing it.
- **`wslpath` loses backslashes or `.Trim()` fails:** use `--exec` and check its
  exit code as shown above. A manually specified `/mnt/c/.../arena-gpu-v2.tar`
  path is also valid if it points to the saved archive.
- **ImagePullBackOff:** confirm the exact image tag was imported into
  `Ubuntu-24.04` in containerd's `k8s.io` namespace. A local Docker/Podman image
  alone is not visible to Kubernetes.
- **Rollout waits with zero new replicas:** inspect
  `kubectl -n arena-gpu describe deployment arena-dashboard`,
  `kubectl -n arena-gpu get pods -o wide`, and namespace events. Recreate waits
  for old pods to terminate. Check termination, scheduling, image availability,
  and startup logs before assuming the cluster is broken.
- **Pending / insufficient nvidia.com/gpu:** another workload may already hold
  the GPU. Completed worker pods release their GPU when they terminate.
- **CUDA provider initialization failed:** inspect worker logs for missing
  libraries, driver incompatibility, or model/operator errors. This version
  deliberately fails instead of reporting a CPU-only run as a GPU benchmark.
  If `libcuda.so.1` is missing while CUDA/cuDNN libraries are present, verify the
  worker pod has `runtimeClassName: nvidia` and that the cluster's NVIDIA runtime
  is configured. Do not copy host driver libraries into the application image.
- **GPU out of memory:** inspect `nvidia-smi` for other GPU users. Large models
  may exceed the laptop's VRAM even when Kubernetes exposes one GPU.
- **Download blocked:** request approved access or a mirror from IT. Build-time
  Python feed settings do not configure runtime dataset/model downloads.
- **ONNX warning about `/sys/class/drm/card0/device/vendor`:** this was observed
  under WSL even when CUDA embedding succeeded. Use the loaded session's
  providers and completion logs to judge GPU initialization.
- **Some nodes not assigned to the preferred provider:** ONNX can deliberately
  keep shape/housekeeping operations on CPU. This warning alone is not a
  CPU-only fallback.
- **Hugging Face unauthenticated warning:** it concerns download rate limits,
  not GPU selection. Do not commit access tokens to the repo or image.

## Data and access

Postgres uses `emptyDir`; model/dataset caches use `/var/lib/arena-cache` on the
node and are not shared across nodes. RESET deletes the GPU Arena's database
and results but does not touch the CPU Arena's namespace or clear the cache.
The dashboard has no user authentication and uses demo database credentials.
Keep access local through the loopback-bound port-forward; do not expose this
demo publicly or use it for sensitive data.

## References

- [Original CPU Arena and benchmark methodology](../aks-desktop/README.md)
- [FastEmbed GPU setup](https://qdrant.github.io/fastembed/examples/FastEmbed_GPU/)
- [ONNX Runtime CUDA requirements](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#requirements)
