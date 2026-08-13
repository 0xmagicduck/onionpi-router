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
    [string]$SourceRoot = "",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ouvrez PowerShell en tant qu'administrateur."
}
if ($TokenStdin) {
    if ($Token) { throw "Choisissez -Token ou -TokenStdin." }
    $secure = Read-Host -AsSecureString "Jeton du noeud"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $Token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if ($Node -notmatch '^[0-9a-f]{16}$') { throw "Identifiant de noeud invalide." }
if ($Token -notmatch '^[0-9a-f]{64}$') { throw "Jeton invalide." }
if ($Port -lt 1 -or $Port -gt 65535) { throw "Port invalide." }
if ($ClientKey -and $ClientKey -notmatch '^[A-Z2-7]{52}$') { throw "Cle client invalide." }
if ($MeshPort -lt 1 -or $MeshPort -gt 65535 -or $MeshPort -eq $Port) { throw "MeshPort invalide." }
if ($CoordinatorKey -and $CoordinatorKey -notmatch '^ed25519:[0-9a-f]{64}$') {
    throw "Cle de coordinateur invalide."
}
if ($MeshLock) {
    if (-not $CoordinatorKey) { throw "MeshLock sans CoordinatorKey ne verrouille rien." }
    if ($MeshLock -notmatch '^([1-8]):(ed25519:[0-9a-f]{64}(,ed25519:[0-9a-f]{64})*)$') {
        throw "MeshLock invalide: attendu K:cle,cle,..."
    }
    $lockThreshold = [int]$Matches[1]
    $lockTrustees = $Matches[2] -split ','
    if ($lockThreshold -gt $lockTrustees.Count) { throw "Seuil du verrou trop eleve." }
}
if ($ClientName -notmatch '^[A-Za-z0-9_-]{1,32}$') { throw "Nom client invalide." }

if (-not $Yes) {
    $reply = Read-Host "Installer Tor, Python et l'agent OnionPi sur cette machine ? [o/N]"
    if ($reply -notmatch '^[oOyY]$') { Write-Host "Abandon."; exit 0 }
}

$source = $SourceRoot
if (-not $source) { $source = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $source -or -not (Test-Path -LiteralPath $source)) { throw "Source de l'agent absente." }
$base = Join-Path $env:ProgramData "OnionPi Node"
$lib = Join-Path $base "lib"
$state = Join-Path $base "state"
$result = Join-Path $base "result"
$logDir = Join-Path $base "log"
$torRoot = Join-Path $base "tor"
$torData = Join-Path $base "tor-data"
$hiddenService = Join-Path $base "hidden-service"
$config = Join-Path $base "agent.env"

# Releases before 0.4.2 could leave Windows with DefaultOutboundAction=Block
# even though no transparent transport existed. A reinstall is an explicit
# recovery action: restore the snapshot before doing anything that needs the
# network, then remove the obsolete rules.
$savedFirewall = Join-Path $result "firewall-profiles.json"
if (Test-Path -LiteralPath $savedFirewall) {
    $saved = Get-Content -LiteralPath $savedFirewall -Raw | ConvertFrom-Json
    foreach ($profile in @($saved.Profiles)) {
        Set-NetFirewallProfile -Name $profile.Name -DefaultOutboundAction $profile.DefaultOutboundAction
    }
    foreach ($ruleName in @($saved.OutboundAllowRules)) {
        Enable-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    }
}
Get-NetFirewallRule -Group "OnionPi Node" -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Write-Host "> Python"
function Resolve-SystemPython {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $candidate = (& py.exe -3 -c "import sys; print(sys.executable)").Trim()
        if ($candidate -and $candidate.StartsWith($env:ProgramFiles) -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    $candidate = Get-ChildItem -Path $env:ProgramFiles -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '[\\/]Python3[0-9]+[\\/]python\.exe$' } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($candidate) { return $candidate.FullName }
    return $null
}
$python = Resolve-SystemPython
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "winget est requis pour installer Python automatiquement."
    }
    & winget.exe install --id Python.Python.3.13 -e --source winget --scope machine `
        --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -notin @(0, 3010)) { throw "Installation de Python refusee." }
    $python = Resolve-SystemPython
}
if (-not (Test-Path -LiteralPath $python)) { throw "Python reste introuvable." }

Write-Host "> Tor Expert Bundle officiel"
New-Item -ItemType Directory -Force -Path $base, $lib, $state, $result, $logDir, $torRoot, $torData, $hiddenService | Out-Null
$torVersion = "15.0.19"
$torArchive = Join-Path $env:TEMP "tor-expert-bundle-$torVersion.tar.gz"
$torUrl = "https://archive.torproject.org/tor-package-archive/torbrowser/$torVersion/tor-expert-bundle-windows-x86_64-$torVersion.tar.gz"
$torSha256 = "6ac067402c7b4a3dc37887ed3754b3914b67fdc220c966190683e9ccf91abf0f"
& curl.exe --proto '=https' --tlsv1.2 -fsSL $torUrl -o $torArchive
if ($LASTEXITCODE -ne 0) { throw "Telechargement de Tor refuse." }
if ((Get-FileHash -LiteralPath $torArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $torSha256) {
    throw "L'archive Tor ne correspond pas a l'empreinte attendue."
}
Remove-Item -LiteralPath $torRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $torRoot | Out-Null
& tar.exe -xzf $torArchive -C $torRoot
Remove-Item -LiteralPath $torArchive -Force
$tor = Get-ChildItem -Path $torRoot -Filter tor.exe -Recurse | Select-Object -First 1
if (-not $tor) { throw "tor.exe est absent de l'archive officielle." }
Set-Content -LiteralPath (Join-Path $base "tor-path.txt") -Value $tor.FullName -Encoding ASCII

Write-Host "> Agent et identite"
Copy-Item -LiteralPath (Join-Path $source "onionpi-node-agent.py") -Destination $lib -Force
Copy-Item -LiteralPath (Join-Path $source "onionpi_mesh.py") -Destination $lib -Force
Copy-Item -LiteralPath (Join-Path $source "onionpi_mesh_runtime.py") -Destination $lib -Force
Copy-Item -LiteralPath (Join-Path $source "onionpi-node-apply-windows.ps1") -Destination $lib -Force
@(
    "NODE_ID=$Node", "TOKEN=$Token", "PORT=$Port", "MESH_PORT=$MeshPort",
    "COORDINATOR_KEY=$CoordinatorKey"
) | Set-Content -LiteralPath $config -Encoding ASCII

# Ecrit par SYSTEM, lu par l'agent: un verrou que le coordinateur pourrait
# remplacer ne verrouillerait rien.
$meshLock = Join-Path $base "mesh.lock"
if ($MeshLock) {
    $document = @{
        version   = 1
        threshold = $lockThreshold
        trustees  = @($lockTrustees)
    } | ConvertTo-Json -Compress
    Set-Content -LiteralPath $meshLock -Value $document -Encoding ASCII
}
elseif (Test-Path -LiteralPath $meshLock) {
    Remove-Item -LiteralPath $meshLock -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $state "apply.request"))) {
    New-Item -ItemType File -Path (Join-Path $state "apply.request") | Out-Null
}
if (-not (Test-Path -LiteralPath (Join-Path $result "apply.result"))) {
    New-Item -ItemType File -Path (Join-Path $result "apply.result") | Out-Null
}

$authDir = Join-Path $hiddenService "authorized_clients"
if ($ClientKey) {
    New-Item -ItemType Directory -Force -Path $authDir | Out-Null
    Set-Content -LiteralPath (Join-Path $authDir "$ClientName.auth") `
        -Value "descriptor:x25519:$ClientKey" -Encoding ASCII
}
elseif (Test-Path -LiteralPath $authDir) {
    Remove-Item -LiteralPath $authDir -Recurse -Force
}

$torrc = Join-Path $base "torrc"
$torDataConfig = $torData.Replace('\', '/')
$hiddenConfig = $hiddenService.Replace('\', '/')
$torLogConfig = (Join-Path $logDir "tor.log").Replace('\', '/')
@(
    "DataDirectory `"$torDataConfig`"",
    "SocksPort 127.0.0.1:9050",
    "ControlPort 127.0.0.1:9051",
    "CookieAuthentication 1",
    "CookieAuthFile `"$torDataConfig/control.authcookie`"",
    "HiddenServiceDir `"$hiddenConfig`"",
    "HiddenServiceVersion 3",
    "HiddenServicePort $Port 127.0.0.1:$Port",
    "HiddenServicePort $MeshPort 127.0.0.1:$MeshPort",
    "Log notice file `"$torLogConfig`""
) | Set-Content -LiteralPath $torrc -Encoding ASCII

# SYSTEM retains Tor and the privileged helper. LOCAL SERVICE only receives
# the configuration, log and request directories needed by the agent.
& icacls.exe $base /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:RX' | Out-Null
& icacls.exe $hiddenService /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /T | Out-Null
& icacls.exe $torData /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /T | Out-Null
& icacls.exe $lib /grant:r '*S-1-5-19:(OI)(CI)RX' | Out-Null
& icacls.exe $config /grant:r '*S-1-5-19:R' | Out-Null
& icacls.exe $state /grant:r '*S-1-5-19:(OI)(CI)M' | Out-Null
& icacls.exe $logDir /grant:r '*S-1-5-19:(OI)(CI)M' | Out-Null
& icacls.exe $result /grant:r '*S-1-5-19:(OI)(CI)RX' | Out-Null

Write-Host "> Services Windows"
$torAction = New-ScheduledTaskAction -Execute $tor.FullName -Argument "-f `"$torrc`"" -WorkingDirectory $tor.DirectoryName
$startup = New-ScheduledTaskTrigger -AtStartup
$systemPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName "OnionPi Node Tor" -Action $torAction -Trigger $startup `
    -Principal $systemPrincipal -Force | Out-Null
Start-ScheduledTask -TaskName "OnionPi Node Tor"

$cookie = Join-Path $torData "control.authcookie"
for ($index = 0; $index -lt 60 -and -not (Test-Path -LiteralPath $cookie); $index++) {
    Start-Sleep -Seconds 1
}
if (-not (Test-Path -LiteralPath $cookie)) { throw "Tor n'a pas cree son cookie de controle." }
& icacls.exe $cookie /grant:r '*S-1-5-19:R' | Out-Null

$agentWrapper = Join-Path $base "agent.cmd"
@(
    '@echo off',
    "set `"ONIONPI_NODE_CONFIG=$config`"",
    "set `"ONIONPI_NODE_STATE=$state`"",
    "set `"ONIONPI_NODE_RESULT=$result`"",
    "set `"ONIONPI_NODE_TOR_COOKIE=$cookie`"",
    "set `"ONIONPI_NODE_LOG_DIR=$logDir`"",
    "set `"ONIONPI_NODE_PORT=$Port`"",
    "set `"ONIONPI_NODE_MESH_PORT=$MeshPort`"",
    "set `"ONIONPI_NODE_MESH_LOCK=$meshLock`"",
    'set "ONIONPI_NODE_APPLY_TIMEOUT=75"',
    "`"$python`" `"$(Join-Path $lib 'onionpi-node-agent.py')`" >> `"$(Join-Path $logDir 'agent.log')`" 2>&1"
) | Set-Content -LiteralPath $agentWrapper -Encoding ASCII
& icacls.exe $agentWrapper /grant:r '*S-1-5-19:R' | Out-Null
$agentAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/d /c `"$agentWrapper`"" -WorkingDirectory $base
$servicePrincipal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\LOCAL SERVICE" -LogonType ServiceAccount -RunLevel Limited
Register-ScheduledTask -TaskName "OnionPi Node Agent" -Action $agentAction -Trigger $startup `
    -Principal $servicePrincipal -Force | Out-Null

$applyScript = Join-Path $lib "onionpi-node-apply-windows.ps1"
$applyAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$applyScript`"" -WorkingDirectory $base
$applyTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName "OnionPi Node Apply" -Action $applyAction -Trigger $applyTrigger `
    -Principal $systemPrincipal -Force | Out-Null
Start-ScheduledTask -TaskName "OnionPi Node Apply"
Start-ScheduledTask -TaskName "OnionPi Node Agent"

Write-Host "> Publication"
$hostname = Join-Path $hiddenService "hostname"
for ($index = 0; $index -lt 60 -and -not (Test-Path -LiteralPath $hostname); $index++) {
    Start-Sleep -Seconds 1
}
if (-not (Test-Path -LiteralPath $hostname)) { throw "Tor n'a pas encore publie l'adresse onion." }
$address = (Get-Content -LiteralPath $hostname -Raw).Trim()

Write-Host ""
Write-Host "Agent installe sur Windows."
Write-Host ""
Write-Host "  Adresse du noeud : $address"
Write-Host "  Port de l'agent : $Port"
Write-Host ""
Write-Host "Recopiez cette adresse dans Baie virtuelle, puis actualisez le noeud."
Write-Host "Windows reste en sortie directe; Tor-only est refuse sans tunnel TUN verifie."
