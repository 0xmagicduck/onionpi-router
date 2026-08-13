# Windows PowerShell 5.1 reads UTF-8 without BOM using the legacy system code
# page. Keep the full Windows chain ASCII-only and parse the installer from
# memory so the host never guesses its encoding.
#requires -Version 5.1

param(
    [Parameter(Mandatory = $true)][string]$Node,
    [string]$Token = "",
    [switch]$TokenStdin,
    [int]$Port = 9080,
    [int]$MeshPort = 9081,
    [string]$ClientKey = "",
    [string]$ClientName = "baie",
    [string]$CoordinatorKey = "",
    [string]$MeshLock = "",
    [string]$Repository = "0xmagicduck/onionpi-router",
    [string]$Ref = "main",
    [string]$BundleDigest = "",
    [switch]$UnverifiedBundle,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "Depot GitHub invalide." }
if ($Ref -notmatch '^[A-Za-z0-9._/-]+$' -or $Ref.Contains('..') -or $Ref.StartsWith('-')) {
    throw "Reference GitHub invalide."
}
if ($BundleDigest -and $BundleDigest -notmatch '^[0-9a-f]{64}$') {
    throw "Empreinte de paquet invalide."
}
if (-not $BundleDigest -and -not $UnverifiedBundle) {
    throw ("Aucune empreinte a verifier. Copiez la commande complete depuis " +
        "'Baie virtuelle > Preparer l'installation'. Sans appliance de reference, " +
        "passez -UnverifiedBundle explicitement.")
}
if ($TokenStdin) {
    if ($Token) { throw "Choisissez -Token ou -TokenStdin." }
    # Saisi sans echo et garde en memoire du processus: l'installateur est
    # execute dans ce meme processus, donc le jeton n'atteint jamais une ligne
    # de commande visible par les autres comptes de la machine.
    $secure = Read-Host -AsSecureString "Jeton du noeud"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $Token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if ($Token -notmatch '^[0-9a-f]{64}$') { throw "Jeton invalide." }

# Le meme manifeste que `bundle_digest` cote baie: une ligne "empreinte  chemin"
# par fichier, chemins relatifs en barres obliques, tries en ordre d'octets, le
# tout condense une fois.
function Get-BundleDigest {
    param([string]$Root)
    $files = @(Get-ChildItem -LiteralPath $Root -Recurse -File)
    $names = [string[]]@($files | ForEach-Object {
        $_.FullName.Substring($Root.Length).TrimStart('\', '/').Replace('\', '/')
    })
    [Array]::Sort($names, [StringComparer]::Ordinal)
    $manifest = New-Object Text.StringBuilder
    foreach ($name in $names) {
        $path = Join-Path $Root ($name -replace '/', '\')
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        [void]$manifest.Append($hash + "  " + $name + "`n")
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($manifest.ToString())
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
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

    if ($BundleDigest) {
        $found = Get-BundleDigest -Root $installer.DirectoryName
        if ($found -ne $BundleDigest) {
            throw ("L'agent telecharge n'est pas celui de votre baie. Attendu " +
                "$BundleDigest, obtenu $found. Rien n'a ete execute: relancez la " +
                "commande affichee par 'Preparer l'installation', ou utilisez " +
                "l'archive hors ligne.")
        }
        Write-Host "> Empreinte du paquet verifiee"
    }
    else {
        Write-Warning "Paquet non verifie (-UnverifiedBundle)"
    }

    $arguments = @{
        Node = $Node
        Token = $Token
        Port = $Port
        MeshPort = $MeshPort
        ClientKey = $ClientKey
        ClientName = $ClientName
        CoordinatorKey = $CoordinatorKey
        MeshLock = $MeshLock
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
