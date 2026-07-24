$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not $env:PROCESSOR_ARCHITECTURE) {
    $env:PROCESSOR_ARCHITECTURE = if ([Environment]::Is64BitOperatingSystem) { "AMD64" } else { "x86" }
}

$deploy = Get-Command pyside6-deploy -ErrorAction Stop
Write-Host "Using $($deploy.Source)"
& $deploy.Source -c "$projectRoot\pysidedeploy.spec" -f
if ($LASTEXITCODE -ne 0) {
    throw "pyside6-deploy failed with exit code $LASTEXITCODE"
}

$executable = Get-ChildItem -LiteralPath "$projectRoot\dist" -Filter "CoursewareAgentConsole.exe" -Recurse | Select-Object -First 1
if (-not $executable) {
    throw "Build completed without CoursewareAgentConsole.exe"
}
Write-Host "Built $($executable.FullName)"
