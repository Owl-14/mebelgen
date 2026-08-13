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

$basisRoot = Split-Path -Parent $PSScriptRoot
if (-not $MaterialBasePath) {
    $MaterialBasePath = Join-Path $basisRoot "materials\baza_materiala.json"
}
if (-not $ImporterScriptPath) {
    $ImporterScriptPath = Join-Path $PSScriptRoot "ImportFurnitureFromJSON.js"
}

# Windows PowerShell 5.1 treats a UTF-8 script without BOM as an ANSI file.
# Build the Cyrillic vendor token from code points so identity checks do not
# depend on the console/script encoding.
$basisCyrillic = -join @([char]0x411, [char]0x410, [char]0x417, [char]0x418, [char]0x421)
$centerCyrillic = -join @([char]0x426, [char]0x415, [char]0x41D, [char]0x422, [char]0x420)
$basisProductPattern = "(?i)(BAZIS|BASIS|" + [regex]::Escape($basisCyrillic) + ")"
$basisVendorPattern = "(?i)(BAZIS\s*SOFT|BAZIS[-\s]*(CENTER|CENTRE)|" +
    "BASIS[-\s]*(CENTER|CENTRE)|" + [regex]::Escape($basisCyrillic) +
    "[-\s]*" + [regex]::Escape($centerCyrillic) + ")"

function Get-ObjectProperty($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-CanonicalExistingPath([string]$Path, [string]$PathType = "Any") {
    if (-not $Path) { return $null }
    $testArgs = @{ LiteralPath = $Path }
    if ($PathType -eq "Leaf") { $testArgs.PathType = "Leaf" }
    if ($PathType -eq "Container") { $testArgs.PathType = "Container" }
    if (-not (Test-Path @testArgs)) { return $null }
    $providerPath = (Resolve-Path -LiteralPath $Path).ProviderPath
    $fullPath = [System.IO.Path]::GetFullPath($providerPath)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.Equals($pathRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $pathRoot
    }
    return $fullPath.TrimEnd('\', '/')
}

function Test-PathWithin([string]$Candidate, [string]$Parent) {
    if (-not $Candidate -or -not $Parent) { return $false }
    $candidateFull = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    if ($candidateFull.Equals($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    return $candidateFull.StartsWith(
        $parentFull + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Test-NoReparsePoint([string]$Path, [string]$StopAt) {
    $current = Get-CanonicalExistingPath $Path
    $stop = Get-CanonicalExistingPath $StopAt
    if (-not $current) { return $false }
    while ($current) {
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            return $false
        }
        if ($stop -and $current.Equals($stop, [System.StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $pathRoot = [System.IO.Path]::GetPathRoot($current)
        if ($current.Equals($pathRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) { break }
        $current = $parent.TrimEnd('\', '/')
    }
    return $true
}

function Get-Sha256([string]$Path) {
    $canonical = Get-CanonicalExistingPath $Path "Leaf"
    if (-not $canonical) { return $null }
    return (Get-FileHash -LiteralPath $canonical -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Get-TrustedParents {
    $parents = @(
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "Programs" })
    )
    @($parents | ForEach-Object { Get-CanonicalExistingPath $_ "Container" } |
        Where-Object { $_ } | Sort-Object -Unique)
}

function Get-HklmUninstallEntries {
    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    @(Get-ItemProperty -Path $keys -ErrorAction SilentlyContinue |
        Where-Object {
            ((Get-ObjectProperty $_ "DisplayName") -match $basisProductPattern) -and
            ((Get-ObjectProperty $_ "Publisher") -match $basisVendorPattern)
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

function Add-CandidateRoot(
    [System.Collections.ArrayList]$Accepted,
    [System.Collections.ArrayList]$Rejected,
    [string]$Path,
    [string]$Source,
    [string[]]$TrustedParents,
    [string[]]$TrustedRegistryRoots
) {
    $canonical = Get-CanonicalExistingPath $Path "Container"
    if (-not $canonical) {
        [void]$Rejected.Add([pscustomobject]@{ path = $Path; source = $Source; reason = "not_found" })
        return
    }
    $volumeRoot = [System.IO.Path]::GetPathRoot($canonical)
    $leafName = Split-Path -Leaf $canonical
    if ($canonical.Equals($volumeRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not ($leafName -match $basisProductPattern)) {
        [void]$Rejected.Add([pscustomobject]@{ path = $canonical; source = $Source; reason = "unsafe_root_shape" })
        return
    }

    $scoped = $false
    $scopeRoot = $null
    foreach ($parent in $TrustedParents) {
        if (Test-PathWithin $canonical $parent) { $scoped = $true; $scopeRoot = $parent; break }
    }
    if (-not $scoped) {
        foreach ($registryRoot in $TrustedRegistryRoots) {
            if ($canonical.Equals($registryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                $scoped = $true
                $scopeRoot = $registryRoot
                break
            }
        }
    }
    if (-not $scoped) {
        [void]$Rejected.Add([pscustomobject]@{ path = $canonical; source = $Source; reason = "outside_trusted_scope" })
        return
    }
    if (-not (Test-NoReparsePoint $canonical $null)) {
        [void]$Rejected.Add([pscustomobject]@{ path = $canonical; source = $Source; reason = "reparse_point" })
        return
    }
    if (@($Accepted | Where-Object { $_.path -eq $canonical }).Count -eq 0) {
        [void]$Accepted.Add([pscustomobject]@{ path = $canonical; source = $Source; scopeRoot = $scopeRoot })
    }
}

function Get-BoundedExecutableCandidates([string]$Root) {
    # Deliberately no recursive traversal. Only the canonical install root and
    # a fixed one-level allowlist are inspected.
    $searchDirs = @($Root)
    foreach ($relative in @("Bin", "bin", "Program", "program")) {
        $child = Get-CanonicalExistingPath (Join-Path $Root $relative) "Container"
        if ($child -and (Test-PathWithin $child $Root) -and (Test-NoReparsePoint $child $Root)) {
            $searchDirs += $child
        }
    }
    @($searchDirs | Sort-Object -Unique | ForEach-Object {
        Get-ChildItem -LiteralPath $_ -File -Filter "*.exe" -ErrorAction SilentlyContinue |
            Where-Object {
                (Test-PathWithin $_.FullName $Root) -and
                (Test-NoReparsePoint $_.FullName $Root)
            } |
            ForEach-Object { $_.FullName }
    } | Sort-Object -Unique)
}

function Get-ExecutableEvidence([string]$Path, [string]$InstallRoot) {
    $canonical = Get-CanonicalExistingPath $Path "Leaf"
    if (-not $canonical -or -not (Test-PathWithin $canonical $InstallRoot)) { return $null }

    $item = Get-Item -LiteralPath $canonical -Force
    $versionInfo = $item.VersionInfo
    $signature = Get-AuthenticodeSignature -LiteralPath $canonical
    $subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
    $productName = $versionInfo.ProductName
    $companyName = $versionInfo.CompanyName
    $productVersion = $versionInfo.ProductVersion
    $versionValid = [bool]($productVersion -match "^20[0-9]{2}(\.[0-9]+){1,3}(?:\s|$)")
    $identityValid = [bool](
        $signature.Status -eq [System.Management.Automation.SignatureStatus]::Valid -and
        $subject -match $basisVendorPattern -and
        $productName -match $basisProductPattern -and
        $companyName -match $basisVendorPattern -and
        $versionValid
    )
    [pscustomobject]@{
        path = $canonical
        installRoot = $InstallRoot
        sha256 = Get-Sha256 $canonical
        productName = $productName
        companyName = $companyName
        productVersion = $productVersion
        fileVersion = $versionInfo.FileVersion
        versionValid = $versionValid
        signatureStatus = [string]$signature.Status
        signerSubject = $subject
        identityValid = $identityValid
    }
}

function Get-ScriptCandidates([string]$ExplicitPath) {
    $paths = @()
    if ($ExplicitPath) { $paths += $ExplicitPath }
    $documents = $null
    if ($env:USERPROFILE) {
        $documents = Get-CanonicalExistingPath (Join-Path $env:USERPROFILE "Documents") "Container"
        $paths += Join-Path $env:USERPROFILE "Documents\BazisN\Scripts"
    }
    @($paths | Sort-Object -Unique | ForEach-Object {
        $canonical = Get-CanonicalExistingPath $_ "Container"
        $parentName = if ($canonical) { Split-Path -Leaf (Split-Path -Parent $canonical) } else { $null }
        $trustedScope = [bool](
            $canonical -and $documents -and
            (Test-PathWithin $canonical $documents) -and
            (Split-Path -Leaf $canonical) -eq "Scripts" -and
            $parentName -match $basisProductPattern
        )
        [pscustomobject]@{
            suppliedPath = $_
            path = $canonical
            exists = [bool]$canonical
            trustedScope = $trustedScope
            noReparsePoint = [bool]($trustedScope -and (Test-NoReparsePoint $canonical $null))
        }
    })
}

function Read-MaterialBase([string]$Path) {
    $canonical = Get-CanonicalExistingPath $Path "Leaf"
    if (-not $canonical) {
        return [pscustomobject]@{ path = $Path; valid = $false; error = "file_not_found" }
    }
    try {
        $base = Get-Content -LiteralPath $canonical -Raw -Encoding UTF8 | ConvertFrom-Json
        $items = @(Get-ObjectProperty $base "items")
        $schemaVersion = Get-ObjectProperty $base "schemaVersion"
        $declaredCount = Get-ObjectProperty $base "count"
        [pscustomobject]@{
            path = $canonical
            valid = [bool]($schemaVersion -eq "material-base-v1" -and $items.Count -eq $declaredCount)
            schemaVersion = $schemaVersion
            declaredCount = $declaredCount
            actualCount = $items.Count
            withArticle = @($items | Where-Object { Get-ObjectProperty $_ "article" }).Count
            withBasisName = @($items | Where-Object { Get-ObjectProperty $_ "basisName" }).Count
            edgeItems = @($items | Where-Object { (Get-ObjectProperty $_ "group") -match "^02(?:\s|/)" }).Count
            sha256 = Get-Sha256 $canonical
        }
    } catch {
        [pscustomobject]@{ path = $canonical; valid = $false; error = "invalid_json" }
    }
}

function Read-VerificationManifest(
    [string]$Path,
    $MaterialBase,
    $Importer,
    [object[]]$TrustedExecutables,
    [object[]]$ScriptCandidates,
    [object[]]$AcceptedRoots
) {
    $errors = @()
    if (-not $Path) {
        return [pscustomobject]@{ path = $null; valid = $false; errors = @("not_provided") }
    }
    $canonical = Get-CanonicalExistingPath $Path "Leaf"
    if (-not $canonical) {
        return [pscustomobject]@{ path = $Path; valid = $false; errors = @("file_not_found") }
    }
    try {
        $m = Get-Content -LiteralPath $canonical -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return [pscustomobject]@{ path = $canonical; valid = $false; errors = @("invalid_json") }
    }

    $schemaVersion = Get-ObjectProperty $m "schemaVersion"
    $basisVersion = Get-ObjectProperty $m "basis_version"
    $basisInstallPath = Get-ObjectProperty $m "basis_install_path"
    $basisExecutableSha = Get-ObjectProperty $m "basis_executable_sha256"
    $manifestScriptsPath = Get-ObjectProperty $m "scripts_path"
    $materialSha = Get-ObjectProperty $m "material_base_sha256"
    $materials = @(Get-ObjectProperty $m "materials")
    $edges = @(Get-ObjectProperty $m "edges")
    $jsSmoke = Get-ObjectProperty $m "js_smoke"
    $checkedAt = Get-ObjectProperty $m "checked_at"

    if ($schemaVersion -ne "basis-verification-v1") { $errors += "schema_version_invalid" }

    $matchingExe = @($TrustedExecutables | Where-Object {
        $_.productVersion -eq $basisVersion -and $_.sha256 -eq $basisExecutableSha
    })
    if ($matchingExe.Count -eq 0) { $errors += "executable_evidence_mismatch" }

    $canonicalManifestInstall = Get-CanonicalExistingPath $basisInstallPath "Container"
    if (-not $canonicalManifestInstall -or
        @($AcceptedRoots | Where-Object { $_.path -eq $canonicalManifestInstall }).Count -eq 0 -or
        @($matchingExe | Where-Object { $_.installRoot -eq $canonicalManifestInstall }).Count -eq 0) {
        $errors += "install_path_mismatch"
    }

    $canonicalManifestScripts = Get-CanonicalExistingPath $manifestScriptsPath "Container"
    if (-not $canonicalManifestScripts -or
        @($ScriptCandidates | Where-Object {
            $_.exists -and $_.trustedScope -and $_.noReparsePoint -and $_.path -eq $canonicalManifestScripts
        }).Count -eq 0) {
        $errors += "scripts_path_mismatch"
    }

    if (-not $MaterialBase.valid -or $materialSha -ne $MaterialBase.sha256) {
        $errors += "material_base_hash_mismatch"
    }

    if ($materials.Count -eq 0 -or @($materials | Where-Object {
        -not (Get-ObjectProperty $_ "slot") -or
        -not (Get-ObjectProperty $_ "basisName") -or
        -not (Get-ObjectProperty $_ "article") -or
        $null -eq (Get-ObjectProperty $_ "thickness_mm")
    }).Count -gt 0) { $errors += "materials_invalid" }

    if ($edges.Count -eq 0 -or @($edges | Where-Object {
        -not (Get-ObjectProperty $_ "basisName") -or
        -not (Get-ObjectProperty $_ "article") -or
        $null -eq (Get-ObjectProperty $_ "thickness_mm")
    }).Count -gt 0) { $errors += "edges_invalid" }

    $fixturePath = Get-CanonicalExistingPath (Get-ObjectProperty $jsSmoke "fixture") "Leaf"
    $outputPath = Get-CanonicalExistingPath (Get-ObjectProperty $jsSmoke "output_model") "Leaf"
    if (-not $jsSmoke -or (Get-ObjectProperty $jsSmoke "result") -ne "pass" -or
        (Get-ObjectProperty $jsSmoke "script_sha256") -ne $Importer.sha256 -or
        -not $fixturePath -or (Get-ObjectProperty $jsSmoke "fixture_sha256") -ne (Get-Sha256 $fixturePath) -or
        -not $outputPath -or (Get-ObjectProperty $jsSmoke "output_model_sha256") -ne (Get-Sha256 $outputPath)) {
        $errors += "js_smoke_evidence_invalid"
    }

    $parsedDate = [DateTimeOffset]::MinValue
    if (-not $checkedAt -or -not [DateTimeOffset]::TryParse([string]$checkedAt, [ref]$parsedDate)) {
        $errors += "checked_at_invalid"
    }

    [pscustomobject]@{
        path = $canonical
        valid = [bool]($errors.Count -eq 0)
        errors = $errors
        schemaVersion = $schemaVersion
        basisVersion = $basisVersion
        installPath = $canonicalManifestInstall
        scriptsPath = $canonicalManifestScripts
        materialRows = $materials.Count
        edgeRows = $edges.Count
        jsSmokePassed = [bool]($jsSmoke -and (Get-ObjectProperty $jsSmoke "result") -eq "pass")
        sha256 = Get-Sha256 $canonical
    }
}

$isWindows = [bool]($env:OS -eq "Windows_NT")
$trustedParents = @(if ($isWindows) { Get-TrustedParents })
$uninstallEntries = @(if ($isWindows) { Get-HklmUninstallEntries })
$trustedRegistryRoots = @($uninstallEntries | ForEach-Object {
    Get-CanonicalExistingPath $_.installLocation "Container"
} | Where-Object { $_ } | Sort-Object -Unique)

$acceptedRoots = New-Object System.Collections.ArrayList
$rejectedRoots = New-Object System.Collections.ArrayList
if ($isWindows) {
    foreach ($parent in $trustedParents) {
        Get-ChildItem -LiteralPath $parent -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match $basisProductPattern } |
            ForEach-Object {
                Add-CandidateRoot $acceptedRoots $rejectedRoots $_.FullName "standard_root" $trustedParents $trustedRegistryRoots
            }
    }
    foreach ($entry in $uninstallEntries) {
        if ($entry.installLocation) {
            Add-CandidateRoot $acceptedRoots $rejectedRoots $entry.installLocation "hklm_registry" $trustedParents $trustedRegistryRoots
        }
    }
    if ($BasisInstallPath) {
        Add-CandidateRoot $acceptedRoots $rejectedRoots $BasisInstallPath "explicit" $trustedParents $trustedRegistryRoots
    }
}

$allExecutableEvidence = @()
foreach ($root in @($acceptedRoots)) {
    foreach ($exePath in @(Get-BoundedExecutableCandidates $root.path)) {
        $evidence = Get-ExecutableEvidence $exePath $root.path
        if ($evidence) { $allExecutableEvidence += $evidence }
    }
}
$trustedExecutables = @($allExecutableEvidence | Where-Object { $_.identityValid })
$scriptCandidates = @(Get-ScriptCandidates $ScriptsPath)
$validScriptCandidates = @($scriptCandidates | Where-Object {
    $_.exists -and $_.trustedScope -and $_.noReparsePoint
})
$materialBase = Read-MaterialBase $MaterialBasePath
$importer = [pscustomobject]@{
    path = Get-CanonicalExistingPath $ImporterScriptPath "Leaf"
    exists = [bool](Get-CanonicalExistingPath $ImporterScriptPath "Leaf")
    sha256 = Get-Sha256 $ImporterScriptPath
}
$verification = Read-VerificationManifest $VerificationManifestPath $materialBase $importer `
    $trustedExecutables $scriptCandidates @($acceptedRoots)

$blockers = @()
if (-not $isWindows) { $blockers += "windows_required" }
if ($BasisInstallPath -and @($rejectedRoots | Where-Object { $_.source -eq "explicit" }).Count -gt 0) {
    $blockers += "basis_install_path_untrusted_scope"
}
if (@($acceptedRoots).Count -eq 0) { $blockers += "basis_installation_not_found" }
if ($trustedExecutables.Count -eq 0) { $blockers += "basis_executable_identity_not_confirmed" }
if (@($trustedExecutables | Where-Object { $_.productVersion }).Count -eq 0) { $blockers += "basis_version_not_confirmed" }
if ($validScriptCandidates.Count -eq 0) { $blockers += "basis_scripts_path_not_confirmed" }
if (-not $materialBase.valid) { $blockers += "material_base_invalid" }
if (-not $importer.exists) { $blockers += "importer_script_not_found" }
if (-not $verification.valid) { $blockers += "operator_verification_manifest_missing_or_invalid" }

$report = [pscustomobject]@{
    schemaVersion = "basis-env-preflight-v2"
    generatedAt = (Get-Date).ToUniversalTime().ToString("o")
    mode = "read-only"
    status = if ($blockers.Count -eq 0) { "ready" } else { "blocked" }
    blockers = @($blockers | Sort-Object -Unique)
    windows = [pscustomobject]@{
        isWindows = $isWindows
        osVersion = [Environment]::OSVersion.VersionString
        powershellVersion = $PSVersionTable.PSVersion.ToString()
    }
    basis = [pscustomobject]@{
        trustedParents = $trustedParents
        uninstallEntries = $uninstallEntries
        acceptedRoots = @($acceptedRoots)
        rejectedRoots = @($rejectedRoots)
        executableEvidence = $allExecutableEvidence
        trustedExecutables = $trustedExecutables
        versions = @($trustedExecutables | ForEach-Object { $_.productVersion } | Sort-Object -Unique)
        scriptCandidates = $scriptCandidates
    }
    repository = [pscustomobject]@{ materialBase = $materialBase; importer = $importer }
    operatorVerification = $verification
    prohibitedActionsPerformed = @()
}

if ($Json) { $report | ConvertTo-Json -Depth 10 } else { $report }
if ($blockers.Count -gt 0) { exit 2 }
exit 0
