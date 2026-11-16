# Run once before going on stage:   .\demo-prep.ps1
# Builds the solution, pre-pulls the container images into WSLc, and does a full
# warm-up run (start -> health -> traffic -> stop) so the live run is fast and boring.
# Also makes sure the NPU agent's model is downloaded and compiled for the NPU (cached in models\).
# Use -RequireNpu to fail the pre-show check if the agent falls back to CPU.

param([switch]$RequireNpu)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
. "$PSScriptRoot\demo-env.ps1"

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Magenta }

Step 'Checking WSLc'
wslc info | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'WSLc is not working. Run "wslc info" to troubleshoot.' }

Step 'aspire doctor (container runtime)'
aspire doctor --nologo | Select-String -Pattern 'Container Runtime', 'WSLc', 'Docker', 'Podman'

Step 'Stopping any running AppHost'
aspire stop --nologo 2>$null | Out-Null

Step 'Building'
dotnet build --nologo -v q
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }

Step 'Pre-pulling images into WSLc'
wslc image pull docker.io/library/postgres:18.3
wslc image pull docker.io/library/redis:8.6

Step 'NPU agent: base image + model'
if (-not (wslc image list 2>$null | Select-String 'openvino\s+2026\.2\.1')) {
    throw 'Base image openvino:2026.2.1 is not loaded into WSLc. Run: wslc load -i openvino-2026.2.1.tar'
}
$appSettings = Get-Content "$PSScriptRoot\HotTakes.AppHost\appsettings.json" -Raw | ConvertFrom-Json
$npuModel = $appSettings.NpuAgent.Model
$modelsDir = Join-Path $PSScriptRoot 'models'
New-Item -ItemType Directory -Force $modelsDir | Out-Null
if (Test-Path (Join-Path $modelsDir "$npuModel\openvino_model.xml")) {
    Write-Host "Model $npuModel already present in models\"
}
else {
    Write-Host "Downloading OpenVINO/$npuModel into models\ (several GB, one time)..."
    wslc build -t hottakes-npu-agent "$PSScriptRoot\HotTakes.NpuAgent"
    if ($LASTEXITCODE -ne 0) { throw 'Building the npu-agent image failed.' }
    wslc run --rm -v "${modelsDir}:/models" hottakes-npu-agent python3 /app/download_model.py "OpenVINO/$npuModel" /models
    if ($LASTEXITCODE -ne 0) { throw "Downloading $npuModel failed." }
}

Step 'Warm-up run'
aspire start --nologo --non-interactive
if ($LASTEXITCODE -ne 0) { throw 'aspire start failed.' }
try {
    aspire wait web --timeout 300 --nologo
    if ($LASTEXITCODE -ne 0) { throw 'Resources did not become healthy.' }

    aspire describe --nologo
    wslc list

    $miss = Invoke-RestMethod http://localhost:5050/leaderboard
    $hit = Invoke-RestMethod http://localhost:5050/leaderboard
    Write-Host ("First call: {0} ({1} ms)   Second call: {2} ({3} ms)" -f $miss.source, $miss.elapsedMs, $hit.source, $hit.elapsedMs) -ForegroundColor Green

    # The first NPU compile can take about a minute. The compiled blob is cached in models\.cache, so stage runs are fast.
    Step 'Warming up the NPU agent'
    aspire wait npu-agent --timeout 600 --nologo
    if ($LASTEXITCODE -ne 0) { throw 'npu-agent did not become healthy. Check: aspire logs npu-agent' }
    $bot = Invoke-RestMethod -Method Post http://localhost:5050/bot/generate -TimeoutSec 120
    $color = if ($bot.device -eq 'NPU') { 'Green' } else { 'Yellow' }
    Write-Host ("NPU agent wrote a take on {0} ({1}) in {2} ms: {3}" -f $bot.device, $bot.deviceName, $bot.totalMs, $bot.text) -ForegroundColor $color
    if ($bot.device -ne 'NPU') {
        $status = Invoke-RestMethod http://localhost:5050/bot -TimeoutSec 10
        $reason = $status.agent.fallbackReason
        if ([string]::IsNullOrWhiteSpace($reason)) { $reason = 'Check the NPU driver and "wslc run --gpus all".' }
        if ($RequireNpu) { throw "NPU not used: $reason" }
        Write-Warning "NPU not used: $reason"
    }
}
finally {
    Step 'Stopping warm-up run'
    aspire stop --nologo
}

if ($bot.device -eq 'NPU') {
    Write-Host "`nReady (incl. NPU agent on $npuModel). On stage: . .\demo-env.ps1 ; aspire run" -ForegroundColor Green
} else {
    Write-Host "`nReady on CPU; the NPU needs attention. On stage: . .\demo-env.ps1 ; aspire run" -ForegroundColor Yellow
}
