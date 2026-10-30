<#
.SYNOPSIS
  One-shot, idempotent setup of the Embedding Arena on Windows + WSL.
  Installs Debian (wsl --install), makes root/sudo passwordless, installs microk8s + podman,
  builds and deploys the arena, then opens http://localhost:8080.
#>
param(
  [string]$Distro = "Debian",
  [string]$UserName = $env:USERNAME.ToLower(),
  [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$env:WSL_UTF8 = "1"
$repo = Split-Path -Parent $PSScriptRoot

function Invoke-Wsl([string]$User, [string]$Script) {
  wsl -d $Distro -u $User --exec bash -c $Script
  if ($LASTEXITCODE -ne 0) { throw "WSL command failed ($LASTEXITCODE): $Script" }
}

if (-not ((wsl -l -q) -contains $Distro)) {
  Write-Host "==> wsl --install -d $Distro"
  wsl --install -d $Distro --no-launch
  if ($LASTEXITCODE -ne 0) { throw "wsl --install failed" }
}

Write-Host "==> enabling systemd and default user '$UserName'"
Invoke-Wsl root "id $UserName >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo $UserName; printf '[boot]\nsystemd=true\n\n[user]\ndefault=$UserName\n' > /etc/wsl.conf"
wsl --terminate $Distro | Out-Null

$scripts = (wsl -d $Distro -u root --exec wslpath -a ($PSScriptRoot -replace '\\', '/')).Trim()
Write-Host "==> provisioning $Distro (passwordless root/sudo, microk8s, podman)"
Invoke-Wsl root "bash '$scripts/setup-debian.sh' $UserName"
wsl --terminate $Distro | Out-Null  # pick up new group memberships

Write-Host "==> building and deploying the arena"
Invoke-Wsl $UserName "bash '$scripts/deploy.sh'"

if (-not $NoBrowser) { Start-Process "http://localhost:8080" }
