[CmdletBinding()]
param(
    [string]$BasisInstallPath,
    [string]$ScriptsPath,
    [string]$MaterialBasePath,
    [string]$ImporterScriptPath,
    [string]$VerificationManifestPath,
    [switch]$Json
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$basisCyrillic = -join @([char]0x411, [char]0x410, [char]0x417, [char]0x418, [char]0x421)
$basisNamePattern = "(?i)basis|bazis|" + [regex]::Escape($basisCyrillic)

$basisRoot = Split-Path -Parent $PSScriptRoot
if (-not $MaterialBasePath) {
    $MaterialBasePath = Join-Path $basisRoot "materials\baza_materiala.json"
}
if (-not $ImporterScriptPath) {
    $ImporterScriptPath = Join-Path $PSScriptRoot "ImportFurnitureFromJSON.js"
}

function Resolve-ExistingPath([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Get-ObjectProperty($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-UninstallEntries {
    $uninstallKeys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    @(Get-ItemProperty -Path $uninstallKeys -ErrorAction SilentlyContinue |
        Where-Object {
            ((Get-ObjectProperty $_ "DisplayName") -match $basisNamePattern) -or
            ((Get-ObjectProperty $_ "Publisher") -match $basisNamePattern)
        } |
        ForEach-Object {
            [pscustomobject]@{
                displayName = Get-ObjectProperty $_ "DisplayName"
                displayVersion = Get-ObjectProperty $_ "DisplayVersion"
                publisher = Get-ObjectProperty $_ "Publisher"
                installLocation = Get-ObjectProperty $_ "InstallLocation"
            }
        })
}

function Get-StandardInstallCandidates {
    $roots = @(
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        $env:ProgramData,
        $env:LOCALAPPDATA
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) }

    $found = @()
    foreach ($root in $roots) {
        $found += Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match $basisNamePattern } |
            ForEach-Object { $_.FullName }
    }
    @($found | Sort-Object -Unique)
}

function Get-ExecutableVersions([string[]]$InstallPaths) {
    $result = @()
    foreach ($installPath in $InstallPaths) {
        if (-not (Test-Path -LiteralPath $installPath -PathType Container)) { continue }
        $result += Get-ChildItem -LiteralPath $installPath -Filter "*.exe" -File -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object {
                $v = $_.VersionInfo
                if (($v.ProductName -match $basisNamePattern) -or
                    ($_.Name -match "(?i)basis|bazis|базис|mebel")) {
                    [pscustomobject]@{
                        path = $_.FullName
                        productName = $v.ProductName
                        productVersion = $v.ProductVersion
                        fileVersion = $v.FileVersion
                    }
                }
            }
    }
    @($result | Sort-Object path -Unique)
}

function Get-ScriptCandidates([string]$ExplicitPath) {
    $candidates = @()
    if ($ExplicitPath) { $candidates += $ExplicitPath }
    if ($env:USERPROFILE) {
        $documents = Join-Path $env:USERPROFILE "Documents"
        $candidates += Join-Path $documents "BazisN\Scripts"
        if (Test-Path -LiteralPath $documents -PathType Container) {
            $candidates += Get-ChildItem -LiteralPath $documents -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match ("(?i)^basis|^bazis|^" + [regex]::Escape($basisCyrillic)) } |
                ForEach-Object { Join-Path $_.FullName "Scripts" }
        }
    }
    @($candidates | Sort-Object -Unique | ForEach-Object {
        [pscustomobject]@{
            path = $_
            exists = [bool](Test-Path -LiteralPath $_ -PathType Container)
        }
    })
}

function Read-MaterialBase([string]$Path) {
    $resolved = Resolve-ExistingPath $Path
    if (-not $resolved) {
        return [pscustomobject]@{ path = $Path; valid = $false; error = "file_not_found" }
    }
    try {
        $base = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8 | ConvertFrom-Json
        $items = @(Get-ObjectProperty $base "items")
        $schemaVersion = Get-ObjectProperty $base "schemaVersion"
        $declaredCount = Get-ObjectProperty $base "count"
        return [pscustomobject]@{
            path = $resolved
            valid = [bool]($schemaVersion -eq "material-base-v1" -and $items.Count -eq $declaredCount)
            schemaVersion = $schemaVersion
            declaredCount = $declaredCount
            actualCount = $items.Count
            withArticle = @($items | Where-Object { Get-ObjectProperty $_ "article" }).Count
            withBasisName = @($items | Where-Object { Get-ObjectProperty $_ "basisName" }).Count
            edgeItems = @($items | Where-Object { (Get-ObjectProperty $_ "group") -match "^02(?:\s|/)" }).Count
            sha256 = Get-Sha256 $resolved
        }
    } catch {
        return [pscustomobject]@{ path = $resolved; valid = $false; error = "invalid_json" }
    }
}

function Read-VerificationManifest([string]$Path) {
    if (-not $Path) {
        return [pscustomobject]@{ path = $null; valid = $false; error = "not_provided" }
    }
    $resolved = Resolve-ExistingPath $Path
    if (-not $resolved) {
        return [pscustomobject]@{ path = $Path; valid = $false; error = "file_not_found" }
    }
    try {
        $m = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8 | ConvertFrom-Json
        $materials = @(Get-ObjectProperty $m "materials")
        $edges = @(Get-ObjectProperty $m "edges")
        $materialRowsValid = $materials.Count -gt 0 -and @($materials | Where-Object {
            -not (Get-ObjectProperty $_ "basisName") -or -not (Get-ObjectProperty $_ "article")
        }).Count -eq 0
        $edgeRowsValid = $edges.Count -gt 0 -and @($edges | Where-Object {
            -not (Get-ObjectProperty $_ "basisName") -or
            -not (Get-ObjectProperty $_ "article") -or
            $null -eq (Get-ObjectProperty $_ "thickness_mm")
        }).Count -eq 0
        $jsSmoke = Get-ObjectProperty $m "js_smoke"
        $smokeValid = $jsSmoke -and
            (Get-ObjectProperty $jsSmoke "result") -eq "pass" -and
            (Get-ObjectProperty $jsSmoke "output_model")
        $basisVersion = Get-ObjectProperty $m "basis_version"
        $manifestScriptsPath = Get-ObjectProperty $m "scripts_path"
        $valid = [bool]($basisVersion -and $manifestScriptsPath -and $materialRowsValid -and $edgeRowsValid -and $smokeValid)
        return [pscustomobject]@{
            path = $resolved
            valid = $valid
            basisVersion = $basisVersion
            scriptsPath = $manifestScriptsPath
            materialRows = $materials.Count
            edgeRows = $edges.Count
            jsSmokePassed = [bool]$smokeValid
            sha256 = Get-Sha256 $resolved
        }
    } catch {
        return [pscustomobject]@{ path = $resolved; valid = $false; error = "invalid_json" }
    }
}

$isWindows = [bool]($env:OS -eq "Windows_NT")
$uninstallEntries = @(if ($isWindows) { Get-UninstallEntries })
$installCandidates = @()
if ($BasisInstallPath) { $installCandidates += $BasisInstallPath }
$installCandidates += @($uninstallEntries | ForEach-Object { $_.installLocation } | Where-Object { $_ })
if ($isWindows) { $installCandidates += @(Get-StandardInstallCandidates) }
$installCandidates = @($installCandidates | Sort-Object -Unique)
$existingInstallPaths = @($installCandidates | ForEach-Object { Resolve-ExistingPath $_ } | Where-Object { $_ })
$executables = @(if ($isWindows) { Get-ExecutableVersions $existingInstallPaths })
$scriptCandidates = @(Get-ScriptCandidates $ScriptsPath)
$existingScriptPaths = @($scriptCandidates | Where-Object { $_.exists } | ForEach-Object { $_.path })
$materialBase = Read-MaterialBase $MaterialBasePath
$importer = [pscustomobject]@{
    path = Resolve-ExistingPath $ImporterScriptPath
    exists = [bool](Test-Path -LiteralPath $ImporterScriptPath -PathType Leaf)
    sha256 = Get-Sha256 $ImporterScriptPath
}
$verification = Read-VerificationManifest $VerificationManifestPath

$detectedVersions = @($uninstallEntries | ForEach-Object { $_.displayVersion } | Where-Object { $_ })
$detectedVersions += @($executables | ForEach-Object { $_.productVersion } | Where-Object { $_ })
$detectedVersions = @($detectedVersions | Sort-Object -Unique)

$blockers = @()
if (-not $isWindows) { $blockers += "windows_required" }
if ($existingInstallPaths.Count -eq 0 -and $uninstallEntries.Count -eq 0) { $blockers += "basis_installation_not_found" }
if ($detectedVersions.Count -eq 0) { $blockers += "basis_version_not_confirmed" }
if ($existingScriptPaths.Count -eq 0) { $blockers += "basis_scripts_path_not_confirmed" }
if (-not $materialBase.valid) { $blockers += "material_base_invalid" }
if (-not $importer.exists) { $blockers += "importer_script_not_found" }
if (-not $verification.valid) { $blockers += "operator_verification_manifest_missing_or_invalid" }

$report = [pscustomobject]@{
    schemaVersion = "basis-env-preflight-v1"
    generatedAt = (Get-Date).ToUniversalTime().ToString("o")
    mode = "read-only"
    status = if ($blockers.Count -eq 0) { "ready" } else { "blocked" }
    blockers = $blockers
    windows = [pscustomobject]@{ isWindows = $isWindows; osVersion = [Environment]::OSVersion.VersionString }
    basis = [pscustomobject]@{
        uninstallEntries = $uninstallEntries
        installPaths = $existingInstallPaths
        executables = $executables
        versions = $detectedVersions
        scriptCandidates = $scriptCandidates
    }
    repository = [pscustomobject]@{ materialBase = $materialBase; importer = $importer }
    operatorVerification = $verification
    prohibitedActionsPerformed = @()
}

if ($Json) {
    $report | ConvertTo-Json -Depth 8
} else {
    $report
}

if ($blockers.Count -gt 0) { exit 2 }
exit 0
