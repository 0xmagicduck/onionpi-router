# Windows PowerShell 5.1 reads UTF-8 without BOM using the legacy system code
# page. Keep the full Windows chain ASCII-only and parse the installer from
# memory so the host never guesses its encoding.
#requires -Version 5.1

param(
    [Parameter(Mandatory = $true)][string]$Node,
    [Parameter(Mandatory = $true)][string]$Token,
    [int]$Port = 9080,
    [string]$ClientKey = "",
    [string]$ClientName = "baie",
    [string]$Repository = "0xmagicduck/onionpi-router",
    [string]$Ref = "main",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "Depot GitHub invalide." }
if ($Ref -notmatch '^[A-Za-z0-9._/-]+$' -or $Ref.Contains('..') -or $Ref.StartsWith('-')) {
    throw "Reference GitHub invalide."
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ouvrez PowerShell en tant qu'administrateur, puis relancez la commande."
}

$work = Join-Path ([IO.Path]::GetTempPath()) ("onionpi-node-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $archive = Join-Path $work "source.tar.gz"
    $url = "https://github.com/$Repository/archive/$Ref.tar.gz"
    Write-Host "> Telechargement de l'agent OnionPi ($Ref)"
    & curl.exe --proto '=https' --tlsv1.2 -fsSL $url -o $archive
    if ($LASTEXITCODE -ne 0) { throw "Telechargement GitHub refuse." }
    & tar.exe -xzf $archive -C $work
    if ($LASTEXITCODE -ne 0) { throw "Archive GitHub illisible." }

    $installer = Get-ChildItem -Path $work -Filter install-node-agent-windows.ps1 -Recurse |
        Where-Object { $_.FullName -match '[\\/]packaging[\\/]agent[\\/]' } |
        Select-Object -First 1
    if (-not $installer) { throw "Installateur Windows absent de l'archive." }
    $arguments = @{
        Node = $Node
        Token = $Token
        Port = $Port
        ClientKey = $ClientKey
        ClientName = $ClientName
        SourceRoot = $installer.DirectoryName
        Yes = $Yes
    }
    # Every PowerShell file in packaging/agent is deliberately ASCII. Read and
    # execute the installer from memory so Windows PowerShell 5.1 never gets a
    # second chance to reinterpret its bytes through the active ANSI code page.
    $installerBytes = [IO.File]::ReadAllBytes($installer.FullName)
    foreach ($byte in $installerBytes) {
        if ($byte -gt 127) { throw "Installateur Windows non ASCII refuse." }
    }
    $installerText = [Text.Encoding]::ASCII.GetString($installerBytes)
    $installerBlock = [ScriptBlock]::Create($installerText)
    & $installerBlock @arguments
}
finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
