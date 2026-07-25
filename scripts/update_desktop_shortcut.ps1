$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "课件Agent控制台.lnk"

$exePath = Get-ChildItem -LiteralPath "$projectRoot\dist" -Filter "CoursewareAgentConsole.exe" -Recurse -File | Select-Object -First 1

if (-not $exePath) {
    Write-Host "错误: 找不到 CoursewareAgentConsole.exe，请先运行打包" -ForegroundColor Red
    exit 1
}

Write-Host "找到可执行文件: $($exePath.FullName)"

$WScriptShell = New-Object -ComObject WScript.Shell
$shortcut = $WScriptShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath.FullName
$shortcut.WorkingDirectory = $exePath.Directory.FullName
$shortcut.IconLocation = $exePath.FullName
$shortcut.Description = "课件 Agent 控制台 - UI已更新为清新青翠绿玻璃质感风格"
$shortcut.Save()

Write-Host "桌面快捷方式已更新: $shortcutPath" -ForegroundColor Green
