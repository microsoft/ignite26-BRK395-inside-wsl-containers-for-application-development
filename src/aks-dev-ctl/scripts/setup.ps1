[CmdletBinding()]
param(
  [string]$ClusterName = "embedding-arena",
  [string]$KubernetesVersion = "v1.36.1",
  [string]$AksDesktopPath = (Join-Path $HOME "Downloads\aks-dev-ctl.exe"),
  # Skip the one-time warm-up deploy that caches the Python dependencies on the node.
  [switch]$SkipWarmup
)

# Prepares this machine for the demo. It doesn't leave a cluster running unless one already existed.
#   1. Checks aks-dev-ctl.exe, kubectl and WSL networking.
#   2. Caches the aks-desktop prerequisites (aks-desktop prereq download).
#   3. Warm-up: deploys the project once so the Python dependencies are cached in the WSL distro
#      (/var/lib/arena-cache survives 'cluster delete'). It creates and deletes a temporary cluster if needed.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "$FilePath $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
  }
}

function Test-ClusterExists {
  # Windows PowerShell treats redirected native stderr as an error under "Stop".
  $ErrorActionPreference = "Continue"
  & $AksDesktopPath cluster get-credentials --name $ClusterName --overwrite-existing *> $null
  return $LASTEXITCODE -eq 0
}

Write-Host "==> Checking prerequisites"
if (-not (Test-Path -LiteralPath $AksDesktopPath -PathType Leaf)) {
  throw "aks-dev-ctl.exe was not found at '$AksDesktopPath'. Pass -AksDesktopPath to select its location."
}
if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
  throw "kubectl is required. Install it with: winget install Kubernetes.kubectl"
}
$wslConfig = Join-Path $HOME ".wslconfig"
if (Test-Path -LiteralPath $wslConfig) {
  $line = Select-String -LiteralPath $wslConfig -Pattern '^\s*networkingMode\s*=\s*(\S+)' | Select-Object -Last 1
  if ($line -and $line.Matches[0].Groups[1].Value -ne "nat") {
    throw "$wslConfig sets networkingMode=$($line.Matches[0].Groups[1].Value). aks-desktop needs WSL's default NAT " +
      "networking. Remove that line, run 'wsl --shutdown', then run this script again."
  }
}
Write-Host "    aks-desktop: $AksDesktopPath"
Write-Host "    kubectl:     $((Get-Command kubectl).Source)"
Write-Host "    WSL networking: NAT"

Write-Host "==> Caching aks-desktop prerequisites"
Invoke-Checked $AksDesktopPath @("prereq", "download", "--provider", "wsl")

if (-not $SkipWarmup) {
  $existed = Test-ClusterExists
  if (-not $existed) {
    Write-Host "==> Creating a temporary cluster '$ClusterName' for the warm-up"
    Invoke-Checked $AksDesktopPath @("cluster", "create", "--name", $ClusterName, "--kubernetes-version", $KubernetesVersion, "--provider", "wsl")
  }
  Invoke-Checked "kubectl" @("config", "use-context", $ClusterName)
  Write-Host "==> Deploying the project once to cache its Python dependencies"
  Invoke-Checked "kubectl" @("apply", "-k", $root)
  Invoke-Checked "kubectl" @("-n", "arena", "rollout", "status", "deployment/arena-dashboard", "--timeout=600s")
  if (-not $existed) {
    Write-Host "==> Deleting the temporary cluster (the dependency cache stays in the WSL distro)"
    Invoke-Checked $AksDesktopPath @("cluster", "delete", "--name", $ClusterName, "--prune-kubeconfig")
  }
}

Write-Host ""
Write-Host "Ready for the demo. Follow 'Run the demo' in README.md, starting with:"
Write-Host "  & `"$AksDesktopPath`" cluster create --name $ClusterName --kubernetes-version $KubernetesVersion --provider wsl"
if (-not $SkipWarmup -and $existed) {
  Write-Host "Cluster '$ClusterName' already existed and is still running. Run .\scripts\delete.ps1 first for a clean demo."
}
