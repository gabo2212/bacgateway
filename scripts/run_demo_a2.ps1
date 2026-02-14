param(
    [string]$Config = "configs/demo_a2.yaml"
)

$ErrorActionPreference = "Stop"

if (-not $env:NIAGARA_USER) {
    $env:NIAGARA_USER = Read-Host "Enter NIAGARA_USER"
}

if (-not $env:NIAGARA_PASS) {
    $env:NIAGARA_PASS = Read-Host "Enter NIAGARA_PASS"
}

try {
    $ruleName = "BACnet UDP 47808"
    $hasRule = $false

    if (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue) {
        $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
        if ($existing) {
            $hasRule = $true
        }
    }

    if (-not $hasRule) {
        netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow protocol=UDP localport=47808 | Out-Null
    }
}
catch {
    Write-Warning "Could not ensure firewall rule: $($_.Exception.Message)"
}

python -m gateway.demo_a2_mirror --config $Config
