<#
.SYNOPSIS
    Uninstalls Instagram DNS Sinkhole Scheduled Task and Firewall rules.

.DESCRIPTION
    Cleanly removes:
    - Windows Scheduled Task (IG-DNS-Sinkhole)
    - Windows Firewall rules (UDP/53, TCP/53, TCP/853, TCP/8080)

.REQUIREMENTS
    - Administrator privileges
#>

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$TaskName    = "IG-DNS-Sinkhole"
$FirewallRuleNames = @(
    "IG-DNS-Sinkhole-UDP-53",
    "IG-DNS-Sinkhole-TCP-53",
    "IG-DNS-Sinkhole-TCP-853",
    "IG-DNS-Sinkhole-HTTP-8080"
)

Write-Host "=== Uninstalling Instagram DNS Sinkhole ===" -ForegroundColor Cyan

# Check admin
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERR] Requires Administrator. Run PowerShell as Admin." -ForegroundColor Red
    exit 1
}

# Stop and remove scheduled task
Write-Host "Stopping scheduled task..." -ForegroundColor Yellow
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-Sleep 1

Write-Host "Removing scheduled task registration..." -ForegroundColor Yellow
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Scheduled task removed" -ForegroundColor Green
} else {
    Write-Host "[WARN] Scheduled task may not have existed" -ForegroundColor Yellow
}

# Remove firewall rules
Write-Host "Removing firewall rules..." -ForegroundColor Yellow
foreach ($ruleName in $FirewallRuleNames) {
    Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    Write-Host "[OK] Removed: $ruleName" -ForegroundColor Green
}

Write-Host "`n=== Uninstall Complete ===" -ForegroundColor Cyan
Write-Host "The project files (src/, scripts/, config/, artifacts/, README.md, etc.) were not removed." -ForegroundColor Gray
Write-Host "Delete the project folder manually if desired." -ForegroundColor Gray