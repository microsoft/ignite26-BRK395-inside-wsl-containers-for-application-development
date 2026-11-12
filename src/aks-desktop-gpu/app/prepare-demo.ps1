<#
.SYNOPSIS
  Saves the arena image and imports it into the demo's WSL containerd.
.EXAMPLE
  .\prepare-demo.ps1
.EXAMPLE
  .\prepare-demo.ps1 -Distro dev-cluster -Image docker.io/library/arena:gpu-v2
#>
[CmdletBinding()]
param(
  [ValidateNotNullOrEmpty()]
  [string]$Distro = "dev-cluster",
  [ValidateNotNullOrEmpty()]
  [string]$Image = "docker.io/library/arena:gpu-v2",
  [ValidateNotNullOrEmpty()]
  [string]$ArchivePath = (Join-Path $PSScriptRoot "arena-gpu.tar")
)

$ErrorActionPreference = "Stop"

Get-Command wsl, wslc -ErrorAction Stop | Out-Null
$ArchivePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ArchivePath)

Write-Host "==> checking WSL distribution '$Distro'"
wsl -d $Distro -u root --exec true
if ($LASTEXITCODE -ne 0) {
  throw "Cannot access WSL distribution '$Distro' (exit code $LASTEXITCODE). Check wsl --list --quiet."
}

Write-Host "==> saving $Image to $ArchivePath"
wslc save --output $ArchivePath $Image
if ($LASTEXITCODE -ne 0) {
  throw "Image save failed (exit code $LASTEXITCODE)."
}
if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) {
  throw "Image save did not create '$ArchivePath'."
}

$linuxArchive = wsl -d $Distro -u root --exec wslpath -u $ArchivePath
if ($LASTEXITCODE -ne 0) {
  throw "Archive path conversion failed (exit code $LASTEXITCODE)."
}
if ($linuxArchive -isnot [string] -or [string]::IsNullOrWhiteSpace($linuxArchive)) {
  throw "WSL did not return a single archive path."
}
$linuxArchive = $linuxArchive.Trim()
if (-not $linuxArchive.StartsWith("/")) {
  throw "WSL returned a non-absolute Linux archive path: '$linuxArchive'."
}

Write-Host "==> importing $Image into '$Distro' (namespace k8s.io)"
wsl -d $Distro -u root --exec ctr --namespace k8s.io images import $linuxArchive
if ($LASTEXITCODE -ne 0) {
  throw "Containerd image import failed (exit code $LASTEXITCODE)."
}

Write-Host "==> demo image is ready: $Image"
