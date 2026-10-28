<#
.SYNOPSIS
    Builds the openmoonray WSL container image and exports it to a tar the apps load.

.DESCRIPTION
    Runs `wslc build` against container/Containerfile to produce the
    `openmoonray:latest` image, then `wslc save` to export it to moonray.tar next
    to the built executables so both the C++ renderer and the C# viewer can load
    it into their WSLc sessions.

    This is a from-source OpenMoonRay build and can take a long time on the first
    run (tens of minutes to a few hours). Progress is streamed to the console and
    also captured to container\build.log.

.PARAMETER Tag
    Image tag to build. Default: openmoonray:latest

.PARAMETER OutTar
    Path for the exported tar. Default: container\moonray.tar

.PARAMETER NoCache
    Pass --no-cache to wslc build.
#>
[CmdletBinding()]
param(
    [string]$Tag = "openmoonray:latest",
    [string]$OutTar,
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"
$context = $PSScriptRoot
$logFile = Join-Path $PSScriptRoot "build.log"

# Resolve OutTar here (not in the param default): $PSScriptRoot is not reliably
# populated during parameter binding, which previously yielded '\moonray.tar'.
if ([string]::IsNullOrWhiteSpace($OutTar)) {
    $OutTar = Join-Path $PSScriptRoot "moonray.tar"
}

Write-Host "=== Building WSL container image '$Tag' ===" -ForegroundColor Cyan
Write-Host "Context : $context"
Write-Host "Log     : $logFile"
Write-Host "This is a from-source OpenMoonRay build and may take a long time..." -ForegroundColor Yellow

$buildArgs = @("build", "-t", $Tag, "-f", (Join-Path $context "Containerfile"), "--verbose")
if ($NoCache) { $buildArgs += "--no-cache" }
$buildArgs += $context

# Stream to console and log simultaneously.
& wslc @buildArgs 2>&1 | Tee-Object -FilePath $logFile
if ($LASTEXITCODE -ne 0) {
    Write-Error "wslc build failed with exit code $LASTEXITCODE. See $logFile"
    exit $LASTEXITCODE
}

Write-Host "=== Exporting image to '$OutTar' ===" -ForegroundColor Cyan
& wslc save $Tag -o $OutTar 2>&1 | Tee-Object -FilePath $logFile -Append
if ($LASTEXITCODE -ne 0) {
    Write-Error "wslc save failed with exit code $LASTEXITCODE."
    exit $LASTEXITCODE
}

$sizeGB = [math]::Round((Get-Item $OutTar).Length / 1GB, 2)
Write-Host "=== Done. Image saved to $OutTar ($sizeGB GB) ===" -ForegroundColor Green
