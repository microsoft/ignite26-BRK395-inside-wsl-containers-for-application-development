# Dot-source this before the demo:   . .\demo-env.ps1
# Puts the .NET 10 SDK and Aspire CLI on PATH for this terminal and makes sure Aspire uses WSLc.

$userDotnet = Join-Path $env:LOCALAPPDATA 'Microsoft\dotnet'
if ((Test-Path (Join-Path $userDotnet 'sdk')) -and -not ((& dotnet --list-sdks 2>$null) -match '^10\.')) {
    $env:PATH = "$userDotnet;$env:PATH"
    $env:DOTNET_ROOT = $userDotnet
}

$aspireBin = Join-Path $env:USERPROFILE '.aspire\bin'
if ((Test-Path $aspireBin) -and ($env:PATH -notlike "*$aspireBin*")) {
    $env:PATH = "$aspireBin;$env:PATH"
}

# Aspire auto-detects runtimes in Docker -> Podman -> WSLc order.
# If Docker or Podman is installed, pin WSLc explicitly; otherwise let auto-detection pick it.
if ((Get-Command docker -ErrorAction SilentlyContinue) -or (Get-Command podman -ErrorAction SilentlyContinue)) {
    $env:ASPIRE_CONTAINER_RUNTIME = 'wslc'
    Write-Host "Docker/Podman found -> pinned ASPIRE_CONTAINER_RUNTIME=wslc" -ForegroundColor Yellow
} else {
    Remove-Item Env:ASPIRE_CONTAINER_RUNTIME -ErrorAction SilentlyContinue
    Write-Host "No Docker/Podman -> Aspire will auto-detect WSLc" -ForegroundColor Green
}

Write-Host ("dotnet {0} | aspire {1} | wslc {2}" -f (dotnet --version), (aspire --version), ((wslc --version) -join ' ')) -ForegroundColor Cyan
