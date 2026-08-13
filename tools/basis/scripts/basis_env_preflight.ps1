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
            boardItems = @($items | Where-Object { (Get-ObjectProperty $_ "group") -match "^01(?:\s|/)" }).Count
            edgeItems = @($items | Where-Object { (Get-ObjectProperty $_ "group") -match "^02(?:\s|/)" }).Count
            sha256 = Get-Sha256 $canonical
        }
    } catch {
        [pscustomobject]@{ path = $canonical; valid = $false; error = "invalid_json" }
    }
}

function Test-PositiveFiniteNumber($Value) {
    if ($null -eq $Value -or $Value -is [string] -or -not ($Value -is [ValueType])) {
        return $false
    }
    try { $number = [double]$Value } catch { return $false }
    return [bool]($number -gt 0 -and -not [double]::IsNaN($number) -and -not [double]::IsInfinity($number))
}

function Test-NonNegativeInteger($Value) {
    if ($null -eq $Value -or $Value -is [string] -or -not ($Value -is [ValueType])) {
        return $false
    }
    try { $number = [double]$Value } catch { return $false }
    return [bool](
        $number -ge 0 -and -not [double]::IsNaN($number) -and
        -not [double]::IsInfinity($number) -and $number -eq [Math]::Floor($number)
    )
}

function Read-Bz85U32([byte[]]$Data, [int64]$Offset) {
    if ($Offset -lt 0 -or $Offset + 4 -gt $Data.LongLength) { throw "b3d_unexpected_eof" }
    return [BitConverter]::ToUInt32($Data, [int]$Offset)
}

function Skip-Bz85Node([byte[]]$Data, [int64]$Offset, [int]$Depth = 0) {
    if ($Depth -gt 256 -or $Offset + 9 -gt $Data.LongLength) { throw "b3d_node_invalid" }
    $childCount = Read-Bz85U32 $Data ($Offset + 4)
    if ($childCount -gt 1000000) { throw "b3d_child_count_invalid" }
    $nodeType = [int]$Data[$Offset + 8]
    $p = $Offset + 9
    switch ($nodeType) {
        0 {
            for ($i = 0; $i -lt $childCount; $i++) {
                $p = Skip-Bz85Node $Data $p ($Depth + 1)
            }
        }
        1 { }
        2 { }
        3 { $p += 1 }
        4 { $p += 4 }
        5 { $p += 8 }
        6 {
            $length = Read-Bz85U32 $Data $p
            if ($length -gt 100000000) { throw "b3d_string_length_invalid" }
            $p += 4 + ([int64]$length * 2)
        }
        7 {
            $length = Read-Bz85U32 $Data $p
            if ($length -gt 1000000000) { throw "b3d_blob_length_invalid" }
            $p += 4 + [int64]$length
        }
        9 { $p += 8 }
        default { throw "b3d_node_type_invalid" }
    }
    if ($p -gt $Data.LongLength) { throw "b3d_unexpected_eof" }
    return [int64]$p
}

function Read-Bz85NodeEvidence(
    [byte[]]$Data,
    [int64]$Offset,
    [object[]]$Names,
    [string]$TargetName,
    [int]$Depth = 0
) {
    if ($Depth -gt 256 -or $Offset + 9 -gt $Data.LongLength) { throw "b3d_node_invalid" }
    $nameIndex = Read-Bz85U32 $Data $Offset
    $childCount = Read-Bz85U32 $Data ($Offset + 4)
    if ($childCount -gt 1000000) { throw "b3d_child_count_invalid" }
    $name = if ($nameIndex -eq [uint32]::MaxValue) {
        ""
    } elseif ($nameIndex -lt $Names.Count) {
        [string]$Names[$nameIndex]
    } else {
        throw "b3d_name_index_invalid"
    }
    $nodeType = [int]$Data[$Offset + 8]
    $p = $Offset + 9
    $targetFound = [bool]($name -ceq $TargetName)
    switch ($nodeType) {
        0 {
            for ($i = 0; $i -lt $childCount; $i++) {
                $child = Read-Bz85NodeEvidence $Data $p $Names $TargetName ($Depth + 1)
                $p = $child.endOffset
                if ($child.targetFound) { $targetFound = $true }
            }
        }
        1 { }
        2 { }
        3 { $p += 1 }
        4 { $p += 4 }
        5 { $p += 8 }
        6 {
            $length = Read-Bz85U32 $Data $p
            if ($length -gt 100000000) { throw "b3d_string_length_invalid" }
            $p += 4 + ([int64]$length * 2)
        }
        7 {
            $length = Read-Bz85U32 $Data $p
            if ($length -gt 1000000000) { throw "b3d_blob_length_invalid" }
            $p += 4 + [int64]$length
        }
        9 { $p += 8 }
        default { throw "b3d_node_type_invalid" }
    }
    if ($p -gt $Data.LongLength) { throw "b3d_unexpected_eof" }
    return [pscustomobject]@{
        endOffset = [int64]$p
        name = $name
        targetFound = $targetFound
    }
}

function Get-Adler32([byte[]]$Data) {
    [uint32]$a = 1
    [uint32]$b = 0
    foreach ($value in $Data) {
        $a = ($a + $value) % 65521
        $b = ($b + $a) % 65521
    }
    return [uint32](($b -shl 16) -bor $a)
}

function Expand-B3dDocument([byte[]]$Data, [int64]$PayloadOffset) {
    foreach ($trailerLength in @(0, 64)) {
        $adlerOffset = $Data.LongLength - $trailerLength - 4
        $deflateOffset = $PayloadOffset + 2
        $deflateLength = $adlerOffset - $deflateOffset
        if ($deflateLength -le 0) { continue }
        try {
            [byte[]]$compressed = New-Object byte[] $deflateLength
            [Array]::Copy($Data, $deflateOffset, $compressed, 0, $deflateLength)
            $inputStream = New-Object System.IO.MemoryStream(,$compressed)
            $deflateStream = New-Object System.IO.Compression.DeflateStream(
                $inputStream,
                [System.IO.Compression.CompressionMode]::Decompress
            )
            $outputStream = New-Object System.IO.MemoryStream
            try {
                $deflateStream.CopyTo($outputStream)
                [byte[]]$body = $outputStream.ToArray()
            } finally {
                $deflateStream.Dispose()
                $inputStream.Dispose()
                $outputStream.Dispose()
            }
            [uint32]$expectedAdler = (
                ([uint32]$Data[$adlerOffset] -shl 24) -bor
                ([uint32]$Data[$adlerOffset + 1] -shl 16) -bor
                ([uint32]$Data[$adlerOffset + 2] -shl 8) -bor
                [uint32]$Data[$adlerOffset + 3]
            )
            if ((Get-Adler32 $body) -ne $expectedAdler) { continue }
            return [pscustomobject]@{ body = $body; trailerBytes = $trailerLength; adler32Valid = $true }
        } catch { }
    }
    throw "b3d_document_decompression_invalid"
}

function Read-B3dFileEvidence([string]$Path) {
    $canonical = Get-CanonicalExistingPath $Path "Leaf"
    if (-not $canonical) {
        return [pscustomobject]@{
            path = $Path; exists = $false; extensionValid = $false; structureValid = $false
        }
    }
    $item = Get-Item -LiteralPath $canonical -Force
    $extensionValid = [bool]($item.Extension -ieq ".b3d")
    $magicHex = $null
    $sectionHex = $null
    $flag = $null
    $magicValid = $false
    $sectionValid = $false
    $headerRootName = $null
    $documentSectionFound = $false
    $documentCompressed = $false
    $zlibHeaderValid = $false
    $adler32Valid = $false
    $documentRootName = $null
    $modelNodeFound = $false
    $trailerBytes = $null
    $parseError = $null
    if ($item.Length -gt 268435456) {
        $parseError = "b3d_too_large_for_preflight"
    } else {
        try {
            [byte[]]$data = [System.IO.File]::ReadAllBytes($canonical)
            if ($data.LongLength -lt 9) { throw "b3d_unexpected_eof" }
            $magicHex = [BitConverter]::ToString($data, 0, 4).Replace("-", "")
            $sectionHex = [BitConverter]::ToString($data, 4, 4).Replace("-", "")
            $flag = [int]$data[8]
            $magicValid = [bool]($magicHex -eq "425A3835")
            $sectionValid = [bool]($sectionHex -eq "010000FF" -and $flag -eq 0)
            if (-not $magicValid -or -not $sectionValid) { throw "b3d_header_invalid" }

            $p = [int64]9
            $nameCount = Read-Bz85U32 $data $p
            if ($nameCount -lt 1 -or $nameCount -gt 100000) { throw "b3d_name_count_invalid" }
            $p += 4
            $names = @()
            for ($i = 0; $i -lt $nameCount; $i++) {
                $nameLength = Read-Bz85U32 $data $p
                $p += 4
                if ($nameLength -gt 1048576 -or $p + $nameLength -gt $data.LongLength) {
                    throw "b3d_name_length_invalid"
                }
                $names += [Text.Encoding]::UTF8.GetString($data, [int]$p, [int]$nameLength)
                $p += $nameLength
            }
            $rootNameIndex = Read-Bz85U32 $data $p
            $p = Skip-Bz85Node $data $p
            if ($rootNameIndex -lt $names.Count) { $headerRootName = $names[$rootNameIndex] }
            if ($p + 7 + 64 -gt $data.LongLength) { throw "b3d_document_section_missing" }
            $secondMarker = [BitConverter]::ToString($data, [int]$p, 4).Replace("-", "")
            $secondFlag = [int]$data[$p + 4]
            $documentSectionFound = [bool]($secondMarker -eq "010000FF")
            $documentCompressed = [bool]($documentSectionFound -and $secondFlag -eq 1)
            $cmf = [int]$data[$p + 5]
            $flg = [int]$data[$p + 6]
            $zlibHeaderValid = [bool](($cmf -band 15) -eq 8 -and ((256 * $cmf + $flg) % 31) -eq 0)
            if (-not $documentCompressed -or -not $zlibHeaderValid) { throw "b3d_document_header_invalid" }
            $expanded = Expand-B3dDocument $data ($p + 5)
            $adler32Valid = $expanded.adler32Valid
            $trailerBytes = $expanded.trailerBytes
            [byte[]]$documentBody = $expanded.body
            $documentNameCount = Read-Bz85U32 $documentBody 0
            if ($documentNameCount -lt 1 -or $documentNameCount -gt 100000) {
                throw "b3d_document_name_count_invalid"
            }
            $q = [int64]4
            $documentNames = @()
            for ($i = 0; $i -lt $documentNameCount; $i++) {
                $nameLength = Read-Bz85U32 $documentBody $q
                $q += 4
                if ($nameLength -gt 1048576 -or $q + $nameLength -gt $documentBody.LongLength) {
                    throw "b3d_document_name_length_invalid"
                }
                $documentNames += [Text.Encoding]::UTF8.GetString(
                    $documentBody, [int]$q, [int]$nameLength
                )
                $q += $nameLength
            }
            $documentTree = Read-Bz85NodeEvidence $documentBody $q $documentNames "Model"
            if ($documentTree.endOffset -ne $documentBody.LongLength) {
                throw "b3d_document_trailing_data"
            }
            $documentRootName = $documentTree.name
            $modelNodeFound = $documentTree.targetFound
        } catch {
            $parseError = [string]$_.Exception.Message
        }
    }
    $structureValid = [bool](
        $extensionValid -and $item.Length -ge 128 -and $magicValid -and $sectionValid -and
        $headerRootName -eq "Header" -and $documentSectionFound -and $documentCompressed -and
        $zlibHeaderValid -and $adler32Valid -and $documentRootName -eq "Document" -and
        $modelNodeFound -and $null -ne $trailerBytes -and -not $parseError
    )
    [pscustomobject]@{
        path = $canonical
        exists = $true
        extensionValid = $extensionValid
        fileSizeBytes = [int64]$item.Length
        modelType = if ($magicValid) { "BZ85" } else { $null }
        magicHex = $magicHex
        sectionMarkerHex = $sectionHex
        firstSectionFlag = $flag
        magicValid = $magicValid
        sectionMarkerValid = $sectionValid
        headerRootName = $headerRootName
        documentSectionFound = $documentSectionFound
        documentCompressed = $documentCompressed
        zlibHeaderValid = $zlibHeaderValid
        adler32Valid = $adler32Valid
        documentRootName = $documentRootName
        modelNodeFound = $modelNodeFound
        trailerBytes = $trailerBytes
        parseError = $parseError
        structureValid = $structureValid
        sha256 = Get-Sha256 $canonical
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
    $materialImport = Get-ObjectProperty $m "material_import"
    $materials = @(Get-ObjectProperty $m "materials")
    $edges = @(Get-ObjectProperty $m "edges")
    $jsSmoke = Get-ObjectProperty $m "js_smoke"
    $checkedAt = Get-ObjectProperty $m "checked_at"

    if ($schemaVersion -ne "basis-verification-v2") { $errors += "schema_version_invalid" }

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

    if (-not $materialImport) {
        $errors += "material_import_missing"
    } else {
        if ((Get-ObjectProperty $materialImport "result") -ne "pass" -or
            (Get-ObjectProperty $materialImport "outcome") -ne "completed" -or
            (Get-ObjectProperty $materialImport "method") -ne "basis-material-import") {
            $errors += "material_import_result_invalid"
        }
        $importInstallPath = Get-CanonicalExistingPath `
            (Get-ObjectProperty $materialImport "basis_install_path") "Container"
        if ((Get-ObjectProperty $materialImport "basis_version") -ne $basisVersion -or
            -not $importInstallPath -or $importInstallPath -ne $canonicalManifestInstall) {
            $errors += "material_import_environment_mismatch"
        }
        $importInputPath = Get-CanonicalExistingPath `
            (Get-ObjectProperty $materialImport "input_path") "Leaf"
        if (-not $importInputPath -or $importInputPath -ne $MaterialBase.path -or
            (Get-ObjectProperty $materialImport "input_sha256") -ne $MaterialBase.sha256) {
            $errors += "material_import_input_hash_mismatch"
        }
        $inputCount = Get-ObjectProperty $materialImport "input_count"
        $importedCount = Get-ObjectProperty $materialImport "imported_count"
        $rejectedCount = Get-ObjectProperty $materialImport "rejected_count"
        $boardCount = Get-ObjectProperty $materialImport "board_count"
        $edgeCount = Get-ObjectProperty $materialImport "edge_count"
        if (-not (Test-NonNegativeInteger $inputCount) -or
            -not (Test-NonNegativeInteger $importedCount) -or
            -not (Test-NonNegativeInteger $rejectedCount) -or
            -not (Test-NonNegativeInteger $boardCount) -or
            -not (Test-NonNegativeInteger $edgeCount) -or
            $inputCount -ne $MaterialBase.actualCount -or
            $importedCount -ne $MaterialBase.actualCount -or
            $rejectedCount -ne 0 -or
            $boardCount -ne $MaterialBase.boardItems -or
            $edgeCount -ne $MaterialBase.edgeItems) {
            $errors += "material_import_counts_invalid"
        }
        $importEvidencePath = Get-CanonicalExistingPath `
            (Get-ObjectProperty $materialImport "evidence_path") "Leaf"
        if (-not $importEvidencePath -or
            (Get-ObjectProperty $materialImport "evidence_type") -ne "basis-material-import-report" -or
            (Get-ObjectProperty $materialImport "evidence_sha256") -ne (Get-Sha256 $importEvidencePath) -or
            (Get-Item -LiteralPath $importEvidencePath).Length -le 0) {
            $errors += "material_import_evidence_invalid"
        }
    }

    if ($materials.Count -eq 0) { $errors += "materials_missing" }
    if (@($materials | Where-Object { (Get-ObjectProperty $_ "slot") -cne "board" }).Count -gt 0) {
        $errors += "material_slot_invalid"
    }
    if (@($materials | Where-Object {
        -not (Get-ObjectProperty $_ "basisName") -or
        -not (Get-ObjectProperty $_ "article")
    }).Count -gt 0) { $errors += "materials_identity_invalid" }
    if (@($materials | Where-Object {
        -not (Test-PositiveFiniteNumber (Get-ObjectProperty $_ "thickness_mm"))
    }).Count -gt 0) { $errors += "material_thickness_invalid" }

    if ($edges.Count -eq 0) { $errors += "edges_missing" }
    if (@($edges | Where-Object {
        -not (Get-ObjectProperty $_ "basisName") -or
        -not (Get-ObjectProperty $_ "article")
    }).Count -gt 0) { $errors += "edges_identity_invalid" }
    if (@($edges | Where-Object {
        -not (Test-PositiveFiniteNumber (Get-ObjectProperty $_ "thickness_mm"))
    }).Count -gt 0) { $errors += "edge_thickness_invalid" }

    $fixturePath = Get-CanonicalExistingPath (Get-ObjectProperty $jsSmoke "fixture") "Leaf"
    $outputEvidence = Read-B3dFileEvidence (Get-ObjectProperty $jsSmoke "output_model")
    if (-not $jsSmoke -or (Get-ObjectProperty $jsSmoke "result") -ne "pass" -or
        (Get-ObjectProperty $jsSmoke "script_sha256") -ne $Importer.sha256 -or
        -not $fixturePath -or (Get-ObjectProperty $jsSmoke "fixture_sha256") -ne (Get-Sha256 $fixturePath)) {
        $errors += "js_smoke_evidence_invalid"
    }
    if (-not $outputEvidence.extensionValid) { $errors += "output_model_extension_invalid" }
    if (-not $outputEvidence.magicValid -or -not $outputEvidence.sectionMarkerValid) {
        $errors += "output_model_magic_invalid"
    }
    if ((Get-ObjectProperty $jsSmoke "output_model_sha256") -ne $outputEvidence.sha256) {
        $errors += "output_model_hash_mismatch"
    }
    if (-not $outputEvidence.structureValid -or
        (Get-ObjectProperty $jsSmoke "model_type") -ne $outputEvidence.modelType -or
        (Get-ObjectProperty $jsSmoke "magic_hex") -ne $outputEvidence.magicHex -or
        (Get-ObjectProperty $jsSmoke "section_marker_hex") -ne $outputEvidence.sectionMarkerHex -or
        (Get-ObjectProperty $jsSmoke "file_size_bytes") -ne $outputEvidence.fileSizeBytes -or
        (Get-ObjectProperty $jsSmoke "header_root_name") -ne $outputEvidence.headerRootName -or
        (Get-ObjectProperty $jsSmoke "parser_result") -ne "pass" -or
        (Get-ObjectProperty $jsSmoke "document_root_name") -ne $outputEvidence.documentRootName -or
        (Get-ObjectProperty $jsSmoke "model_node_found") -ne $outputEvidence.modelNodeFound -or
        (Get-ObjectProperty $jsSmoke "document_section_found") -ne $outputEvidence.documentSectionFound -or
        (Get-ObjectProperty $jsSmoke "document_compressed") -ne $outputEvidence.documentCompressed -or
        (Get-ObjectProperty $jsSmoke "trailer_bytes") -ne $outputEvidence.trailerBytes) {
        $errors += "output_model_evidence_invalid"
    }

    $parsedDate = [DateTimeOffset]::MinValue
    if (-not $checkedAt -or -not [DateTimeOffset]::TryParse([string]$checkedAt, [ref]$parsedDate)) {
        $errors += "checked_at_invalid"
    } else {
        $now = [DateTimeOffset]::UtcNow
        if ($parsedDate -lt $now.AddDays(-7)) { $errors += "checked_at_stale" }
        if ($parsedDate -gt $now.AddMinutes(5)) { $errors += "checked_at_in_future" }
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
        materialImportPassed = [bool]($materialImport -and (Get-ObjectProperty $materialImport "result") -eq "pass")
        outputModel = $outputEvidence
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
    schemaVersion = "basis-env-preflight-v3"
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
