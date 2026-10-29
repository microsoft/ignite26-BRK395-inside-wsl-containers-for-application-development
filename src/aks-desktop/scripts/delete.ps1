[CmdletBinding()]
param(
  [string]$ClusterName = "embedding-arena",
  [string]$AksDesktopPath = (Join-Path $HOME "Downloads\aks-desktop.exe"),
  # Also destroy the Ubuntu-24.04 WSL distro (and the cached dependencies, model weights and dataset inside it).
  [switch]$Purge
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $AksDesktopPath -PathType Leaf)) {
  throw "aks-desktop.exe was not found at '$AksDesktopPath'. Pass -AksDesktopPath to select its location."
}

Get-CimInstance Win32_Process -Filter "Name='kubectl.exe'" |
  Where-Object { $_.CommandLine -match 'port-forward' -and $_.CommandLine -match 'svc/arena-dashboard' } |
  ForEach-Object {
    Write-Host "==> Stopping dashboard port-forward (pid $($_.ProcessId))"
    Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
  }

$arguments = @("cluster", "delete", "--name", $ClusterName, "--prune-kubeconfig")
if ($Purge) { $arguments += "--purge" }
Write-Host "==> Deleting cluster '$ClusterName' with aks-desktop"
& $AksDesktopPath @arguments
if ($LASTEXITCODE -ne 0) {
  throw "aks-desktop cluster delete failed with exit code $LASTEXITCODE"
}
Write-Host "Cluster '$ClusterName' deleted. See 'Run the demo' in README.md to create it again."
