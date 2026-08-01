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
$buildInfoPath = Join-Path $projectRoot "resources\build_info.json"
$hadBuildInfo = Test-Path -LiteralPath $buildInfoPath
$previousBuildInfo = if ($hadBuildInfo) {
    [System.IO.File]::ReadAllBytes($buildInfoPath)
} else {
    $null
}
$commitSha = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $commitSha) {
    throw "Unable to determine build commit SHA"
}
$appVersion = (& python -c "from services.build_info import APP_VERSION; print(APP_VERSION)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $appVersion) {
    throw "Unable to determine application version"
}
$buildInfo = [ordered]@{
    version = $appVersion
    commit_sha = $commitSha
    build_date = (Get-Date -Format "yyyy-MM-dd")
}
$buildInfoJson = $buildInfo | ConvertTo-Json

try {
    [System.IO.File]::WriteAllText(
        $buildInfoPath,
        $buildInfoJson + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
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
}
finally {
    if ($hadBuildInfo) {
        [System.IO.File]::WriteAllBytes($buildInfoPath, $previousBuildInfo)
    }
    else {
        Remove-Item -LiteralPath $buildInfoPath -ErrorAction SilentlyContinue
    }
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
