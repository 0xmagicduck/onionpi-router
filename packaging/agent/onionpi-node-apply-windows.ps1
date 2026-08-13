$ErrorActionPreference = "Stop"
$base = Join-Path $env:ProgramData "OnionPi Node"
$state = Join-Path $base "state"
$resultDir = Join-Path $base "result"
$request = Join-Path $state "apply.request"
$result = Join-Path $resultDir "apply.result"
$policyPath = Join-Path $state "policy.json"
$applied = Join-Path $resultDir "policy.applied"
$savedFirewall = Join-Path $resultDir "firewall-profiles.json"
$log = Join-Path $base "log\apply.log"

function Write-Answer([string]$Nonce, [string]$Status, [string]$Message) {
    $temporary = "$result.tmp"
    Set-Content -LiteralPath $temporary -Value "$Nonce $Status $Message" -Encoding ASCII
    Move-Item -LiteralPath $temporary -Destination $result -Force
}

try {
    if (-not (Test-Path -LiteralPath $request)) { exit 0 }
    $line = (Get-Content -LiteralPath $request -Raw).Trim()
    if ($line -notmatch '^([0-9a-f]{8,32}) (policy|restart-tor|reboot)$') { exit 0 }
    $nonce = $Matches[1]
    $action = $Matches[2]

    switch ($action) {
        "policy" {
            if (-not (Test-Path -LiteralPath $policyPath)) {
                Write-Answer $nonce "error" "Aucune politique a appliquer"
                exit 0
            }
            $policy = Get-Content -LiteralPath $policyPath -Raw | ConvertFrom-Json
            # Version 2: elle ajoute les champs du maillage, dont le chemin
            # direct sur bat0, qui n'existe pas sous Windows. Le maillage y
            # passe par Tor et ne demande aucune regle de pare-feu.
            if ($policy.version -ne 2) { throw "Version de politique inconnue" }
            if ($policy.egress -notin @("tor-only", "direct")) { throw "Mode de sortie invalide" }
            if ($policy.exit_country -and $policy.exit_country -notmatch '^[A-Z]{2}$') { throw "Pays invalide" }
            if ($policy.digest -notmatch '^[0-9a-f]{64}$') { throw "Empreinte invalide" }
            $ports = @($policy.keep_open_ports)
            if ($ports.Count -gt 8) { throw "Trop de ports" }
            foreach ($port in $ports) {
                if (($port -isnot [int] -and $port -isnot [long]) -or $port -lt 1 -or $port -gt 65535) {
                    throw "Port invalide"
                }
            }

            if ($policy.egress -eq "direct") {
                Get-NetFirewallRule -Group "OnionPi Node" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
                if (Test-Path -LiteralPath $savedFirewall) {
                    $saved = Get-Content -LiteralPath $savedFirewall -Raw | ConvertFrom-Json
                    foreach ($profile in @($saved.Profiles)) {
                        Set-NetFirewallProfile -Name $profile.Name -DefaultOutboundAction $profile.DefaultOutboundAction
                    }
                    foreach ($ruleName in @($saved.OutboundAllowRules)) {
                        Enable-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
                    }
                }
            }
            else {
                # Windows Filtering Platform can enforce fail-closed egress but
                # cannot transparently turn arbitrary application TCP into
                # SOCKS. Claiming tor-only here used to cut the whole machine
                # off while reporting success. Refuse until a verified TUN
                # transport is installed instead of creating that false state.
                Write-Answer $nonce "error" "Tor-only indisponible sur Windows: aucune interface TUN configuree"
                exit 0
            }
            $document = @{
                digest = $policy.digest
                egress = $policy.egress
                applied_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            } | ConvertTo-Json -Compress
            Set-Content -LiteralPath "$applied.tmp" -Value $document -Encoding UTF8
            Move-Item -LiteralPath "$applied.tmp" -Destination $applied -Force
            Write-Answer $nonce "ok" "Coupe-circuit Windows applique"
        }
        "restart-tor" {
            Stop-ScheduledTask -TaskName "OnionPi Node Tor" -ErrorAction SilentlyContinue
            Get-Process tor -ErrorAction SilentlyContinue | Stop-Process -Force
            Start-ScheduledTask -TaskName "OnionPi Node Tor"
            Write-Answer $nonce "ok" "Tor redemarre"
        }
        "reboot" {
            Write-Answer $nonce "ok" "Redemarrage en cours"
            Restart-Computer -Force
        }
    }
}
catch {
    if ($nonce) { Write-Answer $nonce "error" "Action refusee" }
    Add-Content -LiteralPath $log -Value "$(Get-Date -Format o) $($_.Exception.Message)"
    exit 1
}
