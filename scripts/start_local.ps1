param(
    [string]$PythonPath = "C:\Users\andre\smu_app\venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workerOutputLog = Join-Path $projectRoot "logs\local-image-worker.stdout.log"
$workerErrorLog = Join-Path $projectRoot "logs\local-image-worker.stderr.log"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python executable not found: $PythonPath"
}

New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot "logs") | Out-Null
$env:SMU_SCHEDULER_ENABLED = "false"

$worker = Start-Process `
    -FilePath $PythonPath `
    -ArgumentList "local_carousel_worker.py" `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $workerOutputLog `
    -RedirectStandardError $workerErrorLog `
    -WindowStyle Hidden `
    -PassThru

try {
    & $PythonPath (Join-Path $projectRoot "app.py")
}
finally {
    if (-not $worker.HasExited) {
        Stop-Process -Id $worker.Id
        $worker.WaitForExit()
    }
}
