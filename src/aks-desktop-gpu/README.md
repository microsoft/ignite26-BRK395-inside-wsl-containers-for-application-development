# Embedding Arena on NVIDIA GPU

This is a separate GPU version of [the CPU Arena](../aks-dev-ctl/README.md).
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
- Concurrency defaults to two models and can scale up to four, each reserving one GPU resource.
- Resources use the separate `arena-gpu` namespace. The CPU Arena in `arena` is
  not modified or cleaned up by this dashboard.

CUDA-enabled sessions can still execute some unsupported or housekeeping
operations on CPU. The provider check prevents an entirely CPU-only session;
it does not claim that every operation runs on the GPU.

## Prerequisites

- Windows with WSL 2 available and an NVIDIA GPU supported in WSL.
- A Windows NVIDIA driver compatible with CUDA 12.8 and WSL GPU support.
  Use the Windows driver; do not install a Linux NVIDIA display driver in WSL.
- An existing `wslc` installation for building and saving container images.
- Windows PowerShell and `kubectl`.
- The bundled [`aks-devctl.exe`](k8s/aks-devctl.exe). The commands below use
  this executable directly; if it is on your `PATH`, you can use `aks-devctl`
  instead of `.\k8s\aks-devctl.exe`.
- Network access approved by your organization for the CUDA image, Ubuntu
  packages, cluster artifacts, Python dependencies, Postgres image, dataset,
  and model downloads.
- Cluster policies allowing the worker cache's `hostPath` volumes.

## Create the GPU-enabled cluster

From the repository root, run in **Windows PowerShell**:

```powershell
Set-Location .\src\aks-desktop-gpu

.\k8s\aks-devctl.exe cluster create --name dev-cluster --kubernetes-version v1.36.5 --enable-gpu --nvidia-gpu-sharing 4 --kubeconfig-file dev-kubeconfig
if ($LASTEXITCODE -ne 0) { throw "Cluster creation failed; inspect the command output before continuing." }
```

This creates a local AKS Desktop cluster, not an Azure-hosted AKS cluster.
On Windows, `dev-cluster` is also the WSL instance name. GPU support is enabled,
and NVIDIA time-slicing exposes four schedulable slots per physical GPU.
These slots share the physical GPU's compute and VRAM; they are not four
independent GPUs and do not provide memory or compute isolation.

The generated `dev-kubeconfig` is written in this demo folder. It contains
cluster access credentials: keep it local and do not commit or share it.
The CLI also creates local settings beside its executable; those settings,
kubeconfigs, and image archives are ignored by this demo's `.gitignore`.

Creation rejects an existing WSL instance with the same name and rejects an
existing kubeconfig file unless explicitly authorized to overwrite it. If you
already created this cluster, skip creation and use its generated kubeconfig;
do not delete an existing cluster or overwrite credentials just to rerun this guide.

## Use the generated kubeconfig

Continue in the GPU demo folder:

```powershell
$env:KUBECONFIG = (Resolve-Path .\dev-kubeconfig -ErrorAction Stop).Path
$aksDistro = "dev-cluster"
$image = "docker.io/library/arena:gpu-v2"
$archive = Join-Path $PWD "app\arena-gpu.tar"

kubectl config current-context
kubectl get nodes -o wide
kubectl describe nodes
kubectl get runtimeclass nvidia
wsl --list --verbose
wsl -d $aksDistro --exec /usr/lib/wsl/lib/nvidia-smi
```

Confirm the intended node is **Ready**, advertises four allocatable
`nvidia.com/gpu` slots per physical GPU, and has enough free CPU/memory.
Both the `nvidia` RuntimeClass and the WSL NVIDIA driver must be available
before continuing. With fewer than two free slots, lower **Models in parallel**
in **Settings**; otherwise, excess workers remain pending.

`KUBECONFIG` is set only for this PowerShell session. Repeat that assignment in
each new terminal, from the GPU demo folder. Alternatively, pass the file
explicitly to an individual command:

```powershell
kubectl --kubeconfig .\dev-kubeconfig get pods --all-namespaces
```

If `kubectl` tries `http://localhost:8080/api`, it is not using the intended
kubeconfig; that address is not the Arena's Kubernetes API.

## Build the Arena image

Continue in the same terminal. Use `wslc` as the builder; it defaults to
`linux/amd64`, so no platform parameter is needed. The preparation script does
not build the image: complete this step first.

Use the default Python index, or set an IT-approved package feed. If your
organization blocks `files.pythonhosted.org`, obtain an approved feed before
building; the commented Microsoft proxy is not an instruction to bypass policy.

```powershell
$pipIndexUrl = ""
# Only if approved by IT:
# $pipIndexUrl = "https://packagefeedproxy.microsoft.io/pypi/simple/"

wslc build --build-arg "PIP_INDEX_URL=$pipIndexUrl" -t $image .\app
if ($LASTEXITCODE -ne 0) { throw "Image build failed; do not proceed to save or import." }
```

The CUDA image is significantly larger than the CPU image. If corporate
security blocks a download, use an approved feed or request access from IT.
Do not disable TLS verification or endpoint protection.
The app's `.dockerignore` keeps local executables, settings, kubeconfigs, and
saved image archives out of the image build.

## Prepare the demo environment

Choose **one** of the following paths. Both save the built image as an archive
and import it into the cluster's containerd `k8s.io` namespace. An image in
Docker, Podman, or the builder's image store alone is not available to Kubernetes.

### Use the preparation script

From the GPU demo folder, in the same terminal:

```powershell
.\app\prepare-demo.ps1 -Distro $aksDistro -Image $image -ArchivePath $archive
```

The script stops on WSL, save, path conversion, or import errors.
With the standard names used in this guide, `.\app\prepare-demo.ps1` without
arguments uses the same distro, image tag, and archive location. It does not
create the cluster, build the image, deploy Kubernetes resources, or start
port-forwarding.

### Run the preparation commands manually

Continue in the same terminal. `--exec` bypasses the Linux shell, which otherwise
can strip backslashes from a Windows path passed to `wslpath`. Check the command
result before calling `.Trim()`:

```powershell
wslc save --output $archive $image
if ($LASTEXITCODE -ne 0) { throw "Image save failed." }
if (!(Test-Path -LiteralPath $archive)) { throw "Image archive not found: $archive" }

$linuxArchive = wsl -d $aksDistro --exec wslpath -u "$archive"
if ($LASTEXITCODE -ne 0 -or !$linuxArchive) { throw "Could not convert the archive path for WSL." }
$linuxArchive = $linuxArchive.Trim()

wsl -d $aksDistro --exec test -f "$linuxArchive"
if ($LASTEXITCODE -ne 0) { throw "Archive is not visible inside the cluster distro: $linuxArchive" }

wsl -d $aksDistro -u root --exec ctr --namespace k8s.io images import "$linuxArchive"
if ($LASTEXITCODE -ne 0) { throw "Image import failed." }
```

### Verify the imported image

After either preparation path, check the exact tag:

```powershell
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

## Create the deployment

Apply the supplied manifest rather than a standalone
`kubectl create deployment` command. The manifest creates namespace `arena-gpu`, the dashboard's
ServiceAccount and namespace-scoped RBAC, the `arena-dashboard` Deployment,
and its ClusterIP Service. The dashboard needs that RBAC to create the database,
seeder, and benchmark Jobs.

```powershell
kubectl apply -f .\k8s\arena.yaml
if ($LASTEXITCODE -ne 0) { throw "Deployment failed." }

kubectl -n arena-gpu rollout status deployment/arena-dashboard --timeout=180s
if ($LASTEXITCODE -ne 0) { throw "Dashboard did not become ready; inspect its pod and events." }

kubectl -n arena-gpu get pods
kubectl -n arena-gpu get deployment,service
```

## Set up port-forwarding

In another Windows PowerShell terminal, from the GPU demo folder:

```powershell
$env:KUBECONFIG = (Resolve-Path .\dev-kubeconfig -ErrorAction Stop).Path
kubectl -n arena-gpu port-forward --address 127.0.0.1 service/arena-dashboard 8081:80
```

Keep the terminal open and browse to **http://localhost:8081**. This leaves port
8080 available for the CPU dashboard. Port `8081` on Windows forwards to the
Service's port `80`, which routes to the dashboard container's port `8000`.
Press **Ctrl+C** to stop forwarding; rerun the command if the dashboard pod is
replaced.

## Run the Arena app

Select **START**. The dashboard creates Postgres, seeds the dataset, then runs
two GPU benchmark Jobs at a time by default. Raise **Models in parallel** to
three or four during the run to launch additional queued models without restarting.
Only pods labeled `arena/role=worker` request
the GPU and select `runtimeClassName: nvidia`. The namespace also contains
CPU-based dashboard, seeder, and database pods.

The default corpus is 500 documents, and documents are truncated to 600
characters. To run a larger workload:

1. Open **Settings** using the gear button in the top-right.
2. Set **Corpus size (docs)** to **5000** and wait a second for it to save.
3. Select **RESET**, then **START**. RESET deletes existing results.

The next seed includes up to 5000 documents. Settings survive RESET but not a
dashboard restart. CPU threads per model still control CPU-side processing;
they do not choose a GPU. **Models in parallel** can be set from one to four;
each worker reserves one GPU resource.

## Verify GPU execution

In another Windows PowerShell terminal, from the GPU demo folder, start
monitoring before the benchmark:

```powershell
$env:KUBECONFIG = (Resolve-Path .\dev-kubeconfig -ErrorAction Stop).Path
wsl -d dev-cluster --exec /usr/lib/wsl/lib/nvidia-smi -l 1
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
  exit code as shown above. A manually specified `/mnt/c/.../arena-gpu.tar`
  path is also valid if it points to the saved archive.
- **ImagePullBackOff:** confirm the exact image tag was imported into
  `dev-cluster` in containerd's `k8s.io` namespace. A local Docker/Podman image
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
  may exceed the laptop's VRAM. If allocatable GPU resources share a physical
  GPU, concurrent workers also share its VRAM; reduce **Models in parallel**.
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

- [Original CPU Arena and benchmark methodology](../aks-dev-ctl/README.md)
- [FastEmbed GPU setup](https://qdrant.github.io/fastembed/examples/FastEmbed_GPU/)
- [ONNX Runtime CUDA requirements](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#requirements)
