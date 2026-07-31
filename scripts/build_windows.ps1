$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$projectRootFull = [System.IO.Path]::GetFullPath($projectRoot)
$projectPrefix = $projectRootFull.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
foreach ($outputPath in @(
    (Join-Path $projectRoot "deployment"),
    (Join-Path $projectRoot "dist")
)) {
    $outputFull = [System.IO.Path]::GetFullPath($outputPath)
    if (-not $outputFull.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean build output outside project root: $outputFull"
    }
    if (Test-Path -LiteralPath $outputFull) {
        Remove-Item -LiteralPath $outputFull -Recurse -Force
    }
}

if (-not $env:PROCESSOR_ARCHITECTURE) {
    $env:PROCESSOR_ARCHITECTURE = if ([Environment]::Is64BitOperatingSystem) { "AMD64" } else { "x86" }
}

$deploy = Get-Command pyside6-deploy -ErrorAction Stop
Write-Host "Using $($deploy.Source)"
$localSpec = Join-Path $projectRoot "courseware-agent-deploy-$([guid]::NewGuid().ToString('N')).spec"
Copy-Item -LiteralPath "$projectRoot\pysidedeploy.spec" -Destination $localSpec
try {
    & $deploy.Source -c $localSpec -f
    if ($LASTEXITCODE -ne 0) {
        throw "pyside6-deploy failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $localSpec -ErrorAction SilentlyContinue
}

$executable = Get-ChildItem -LiteralPath "$projectRoot\dist" -Filter "CoursewareAgentConsole.exe" -Recurse | Select-Object -First 1
if (-not $executable) {
    throw "Build completed without CoursewareAgentConsole.exe"
}
$forbiddenDefaultTools = Join-Path $executable.DirectoryName "resources\default_public_tools"
if (Test-Path -LiteralPath $forbiddenDefaultTools) {
    throw "Build unexpectedly contains removed default public tools: $forbiddenDefaultTools"
}
Write-Host "Built $($executable.FullName)"
