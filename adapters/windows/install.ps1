[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
param(
    [Parameter(Mandatory = $true)]
    [string]$SkillPackRoot,

    [Parameter(Mandatory = $true)]
    [string]$Destination
)

$ErrorActionPreference = 'Stop'
$canonicalSkills = @(
    'context-marker',
    'tc-generator',
    'tc-reviewer',
    'tc-to-autotest',
    'autotest-reviewer',
    'orchestrate'
)

$packRoot = [System.IO.Path]::GetFullPath($SkillPackRoot)
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $packRoot 'skills'))
$destinationRoot = [System.IO.Path]::GetFullPath($Destination)
$separator = [System.IO.Path]::DirectorySeparatorChar
$sourcePrefix = $sourceRoot.TrimEnd($separator) + $separator

if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "Source skills directory does not exist: $sourceRoot"
}
if (
    $destinationRoot.Equals($sourceRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $destinationRoot.StartsWith($sourcePrefix, [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw 'Destination must not be inside the source skills directory.'
}

$sourceFiles = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
foreach ($skill in $canonicalSkills) {
    $package = Join-Path $sourceRoot $skill
    if (
        -not (Test-Path -LiteralPath $package -PathType Container) -or
        -not (Test-Path -LiteralPath (Join-Path $package 'SKILL.md') -PathType Leaf)
    ) {
        throw "Missing canonical skill package: $skill"
    }
    Get-ChildItem -LiteralPath $package -Recurse -Force -File |
        Where-Object {
            $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
            $_.Extension -notin @('.pyc', '.pyo') -and
            $_.Name -ne '.DS_Store'
        } |
        ForEach-Object { $sourceFiles.Add($_) }
}

$copied = 0
$unchanged = 0
foreach ($sourceFile in ($sourceFiles | Sort-Object FullName)) {
    $relative = [System.IO.Path]::GetRelativePath($sourceRoot, $sourceFile.FullName)
    $target = Join-Path $destinationRoot $relative
    $same = $false
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $targetFile = Get-Item -LiteralPath $target
        if (
            $targetFile.Length -eq $sourceFile.Length -and
            $targetFile.LastWriteTimeUtc.Ticks -eq $sourceFile.LastWriteTimeUtc.Ticks
        ) {
            $sourceHash = (Get-FileHash -LiteralPath $sourceFile.FullName -Algorithm SHA256).Hash
            $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            $same = $sourceHash -eq $targetHash
        }
    }

    if ($same) {
        $unchanged += 1
        continue
    }

    if ($PSCmdlet.ShouldProcess($target, 'Install portable skill file')) {
        $parent = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $sourceFile.FullName -Destination $target -Force
        [System.IO.File]::SetLastWriteTimeUtc($target, $sourceFile.LastWriteTimeUtc)
        $copied += 1
    }
}

[ordered]@{
    status = if ($WhatIfPreference) { 'planned' } else { 'installed' }
    source = $sourceRoot
    destination = $destinationRoot
    files = $sourceFiles.Count
    copied = $copied
    unchanged = $unchanged
    what_if = [bool]$WhatIfPreference
} | ConvertTo-Json -Compress
