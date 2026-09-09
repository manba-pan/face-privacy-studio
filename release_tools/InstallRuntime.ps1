param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$Manifest,
    [string]$CacheDirectory = (Join-Path $env:LOCALAPPDATA 'FacePrivacyStudio\setup-downloads')
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Safe-Target([string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative) -or ($Relative -split '[\\/]') -contains '..') { throw 'Invalid runtime path' }
    $target = [IO.Path]::GetFullPath((Join-Path $script:InstallRoot $Relative))
    if (-not $target.StartsWith($script:InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime path escapes installation folder' }
    return $target
}
function Matches-Hash([string]$Path, [string]$Expected) {
    if (-not [IO.File]::Exists($Path)) { return $false }
    $hasher = [Security.Cryptography.SHA256]::Create()
    $hashStream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($hasher.ComputeHash($hashStream)).Replace('-','') -eq $Expected) }
    finally { $hashStream.Dispose(); $hasher.Dispose() }
}

try {
    $logFolder = Join-Path $env:LOCALAPPDATA 'FacePrivacyStudio'
    [IO.Directory]::CreateDirectory($logFolder) | Out-Null
    try { Start-Transcript -Path (Join-Path $logFolder 'setup-last.log') -Force | Out-Null } catch { }
    $script:InstallRoot = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    if ($script:InstallRoot.Length -lt 4) { throw 'Choose an application folder, not a drive root' }
    $running = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -eq (Join-Path $script:InstallRoot '视频一键打码工具.exe') } catch { $false }
    }
    if ($running) { throw 'Please close the video redactor before installing an update.' }
    $data = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($data.format -ne 1) { throw 'Unsupported runtime manifest' }
    New-Item -ItemType Directory -Path $CacheDirectory -Force | Out-Null
    $count = 0
    foreach ($package in $data.packages) {
        $count++
        if ($package.filename -ne [IO.Path]::GetFileName($package.filename)) { throw 'Invalid package filename' }
        $uri = [Uri]$package.url
        if ($uri.Scheme -ne 'https' -or $uri.Host -ne 'files.pythonhosted.org') { throw 'Unexpected download origin' }
        Write-Output "[$count/$($data.packages.Count)] $($package.package) $($package.version)"
        $archivePath = Join-Path $CacheDirectory $package.filename
        if (-not (Matches-Hash $archivePath $package.sha256)) {
            # Delete only this installer-owned, hash-invalid cache file.
            if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
            Write-Output "Downloading $([math]::Round($package.bytes / 1MB, 1)) MiB from the official Python package host..."
            & "$env:SystemRoot\System32\curl.exe" --fail --location --proto '=https' --proto-redir '=https' --retry 2 --connect-timeout 20 --speed-time 60 --speed-limit 1024 --output $archivePath --url $package.url
            if ($LASTEXITCODE -ne 0) { throw "Download failed for $($package.package). Re-run setup to retry." }
            if (-not (Matches-Hash $archivePath $package.sha256)) { throw "Package checksum mismatch: $($package.package)" }
        } else { Write-Output 'Using verified local download cache.' }
        $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
        try {
            foreach ($file in $package.files) {
                $target = Safe-Target $file.path
                if (Matches-Hash $target $file.sha256) { continue }
                $entry = $archive.GetEntry($file.entry)
                if ($null -eq $entry) { throw "Missing runtime entry: $($file.entry)" }
                New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($target)) -Force | Out-Null
                $temp = $target + '.setup-' + [Guid]::NewGuid().ToString('N')
                try {
                    $entryStream = $entry.Open()
                    try {
                        $output = [IO.File]::Create($temp)
                        try { $entryStream.CopyTo($output) } finally { $output.Dispose() }
                    } finally { $entryStream.Dispose() }
                    if (-not (Matches-Hash $temp $file.sha256)) { throw "Runtime checksum mismatch: $($file.entry)" }
                    Move-Item -LiteralPath $temp -Destination $target -Force
                } finally {
                    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
                }
            }
        } finally { $archive.Dispose() }
        Write-Output 'Runtime files verified and installed.'
    }
    Write-Output 'All runtime components are ready. No internet is required for video processing.'
    exit 0
} catch {
    Write-Output ('Installation could not finish: ' + $_.Exception.Message)
    exit 1
} finally {
    try { Stop-Transcript | Out-Null } catch { }
}
