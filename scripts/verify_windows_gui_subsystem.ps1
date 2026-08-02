[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$ExecutablePath
)

$ErrorActionPreference = "Stop"

$resolvedPath = [System.IO.Path]::GetFullPath($ExecutablePath)
if (-not [System.IO.File]::Exists($resolvedPath)) {
    throw "Packaged executable was not found: $resolvedPath"
}

$image = [System.IO.File]::ReadAllBytes($resolvedPath)
if ($image.Length -lt 0x40 -or $image[0] -ne 0x4D -or $image[1] -ne 0x5A) {
    throw "The file is not a valid Windows executable: $resolvedPath"
}

$peOffset = [System.BitConverter]::ToInt32($image, 0x3C)
$optionalHeaderOffset = $peOffset + 24
$subsystemOffset = $optionalHeaderOffset + 68
if ($peOffset -lt 0 -or $subsystemOffset + 2 -gt $image.Length) {
    throw "The executable has an invalid PE header: $resolvedPath"
}
if ($image[$peOffset] -ne 0x50 -or $image[$peOffset + 1] -ne 0x45) {
    throw "The executable is missing the PE signature: $resolvedPath"
}

$optionalHeaderMagic = [System.BitConverter]::ToUInt16($image, $optionalHeaderOffset)
if ($optionalHeaderMagic -notin 0x10B, 0x20B) {
    throw "The executable has an unsupported PE optional header: $resolvedPath"
}

$subsystem = [System.BitConverter]::ToUInt16($image, $subsystemOffset)
if ($subsystem -ne 2) {
    throw "Expected a Windows GUI executable (subsystem 2), but found subsystem $subsystem. A console window would appear at startup."
}

Write-Host "Verified Windows GUI subsystem: $resolvedPath"
