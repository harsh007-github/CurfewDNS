<#
.SYNOPSIS
    Installs Instagram Time-Window DNS Sinkhole on Windows 11.

.DESCRIPTION
    This script sets up the DNS sinkhole to run automatically on Windows 11:
    - Installs Python dependencies (dnslib, cryptography)
    - Creates Windows Firewall rules for UDP/53, TCP/53, TCP/853 (DoT), TCP/8080 (HTTP)
    - Generates a self-signed TLS certificate for DoT
    - Registers a startup Scheduled Task so it runs automatically in the background
    - Prints LAN IP and hostname for phone configuration

.REQUIREMENTS
    - Windows 10/11
    - Python 3.8+ in PATH
    - Administrator privileges

.EXAMPLE
    .\setup.ps1

.NOTES
    Run from the scripts/windows/ directory. Project root is two levels up.
#>

# ─── Configuration ────────────────────────────────────────────────────────
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Join-Path $ScriptDir "..\.."
$SrcDir      = Join-Path $ProjectRoot "src"
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"
$PythonExe   = "python"
$TaskName    = "IG-DNS-Sinkhole"
$Description = "Blocks Instagram domains outside 23:30-06:00 via local DNS sinkhole (UDP/TCP/DoT)"
$DnsPort     = 53
$DotPort     = 853
$HttpPort    = 8080

# ─── Helpers ──────────────────────────────────────────────────────────────
function Write-Header   { param($msg) Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Success  { param($msg) Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-ErrorMsg { param($msg) Write-Host "[ERR] $msg" -ForegroundColor Red }
function Write-Warning  { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Info     { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Gray }

# ─── Check Admin ──────────────────────────────────────────────────────────
Write-Header "Checking Administrator Privileges"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-ErrorMsg "This script requires Administrator privileges."
    Write-Info "Right-click PowerShell -> 'Run as Administrator' and try again."
    exit 1
}
Write-Success "Running as Administrator"

# ─── Check Python ─────────────────────────────────────────────────────────
Write-Header "Checking Python Installation"
try {
    $pyVersion = & $PythonExe --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Python not found" }
    Write-Success "Found: $pyVersion"
} catch {
    Write-ErrorMsg "Python not found in PATH. Install from python.org or Microsoft Store."
    exit 1
}

# ─── Install Dependencies ─────────────────────────────────────────────────
Write-Header "Installing Python Dependencies"
$reqFile = Join-Path $ProjectRoot "requirements.txt"
if (Test-Path $reqFile) {
    & $PythonExe -m pip install --quiet -r $reqFile
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Dependencies installed (dnslib, cryptography)"
    } else {
        Write-ErrorMsg "Failed to install dependencies"
        exit 1
    }
} else {
    Write-Warning "requirements.txt not found at $reqFile, skipping pip install"
}

# ─── Verify Script Exists ─────────────────────────────────────────────────
$scriptPath = Join-Path $SrcDir "dns_sinkhole.py"
if (-not (Test-Path $scriptPath)) {
    Write-ErrorMsg "dns_sinkhole.py not found at $scriptPath"
    exit 1
}
Write-Success "Found dns_sinkhole.py at $scriptPath"

# ─── Generate Self-Signed Certificate for DoT ─────────────────────────────
Write-Header "Generating TLS Certificate for DoT (Android Private DNS)"
$certFile = Join-Path $ArtifactsDir "dot_cert.pem"
$keyFile  = Join-Path $ArtifactsDir "dot_key.pem"
if ((Test-Path $certFile) -and (Test-Path $keyFile)) {
    Write-Info "Certificate already exists at $ArtifactsDir, skipping generation"
} else {
    # Ensure artifacts directory exists
    if (-not (Test-Path $ArtifactsDir)) {
        New-Item -ItemType Directory -Path $ArtifactsDir | Out-Null
    }
    try {
        $certScript = @"
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography import x509
from cryptography.x509.oid import NameOID
import datetime

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Instagram DNS Sinkhole')])
cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
    .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName('localhost'),
            x509.DNSName('instagram-blocker.local')
        ]),
        critical=False
    )
    .sign(key, hashes.SHA256())
)

with open(r'$keyFile', 'wb') as f:
    f.write(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    ))
with open(r'$certFile', 'wb') as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))
print('Certificate generated')
"@
        $certScript | & $PythonExe -
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Self-signed certificate generated for DoT at $ArtifactsDir"
        } else {
            Write-Warning "Certificate generation failed (cryptography may not be installed yet). Will retry on first run."
        }
    } catch {
        Write-Warning "Certificate generation error: $_"
    }
}

# ─── Get LAN IP and Hostname ──────────────────────────────────────────────
Write-Header "Detecting LAN IP Address"
$ipConfig = Get-NetIPConfiguration | Where-Object {
    $_.IPv4Address.IPAddress -like "192.168.*" -or
    $_.IPv4Address.IPAddress -like "10.*" -or
    $_.IPv4Address.IPAddress -like "172.16.*"
}
$lanIp = ($ipConfig.IPv4Address.IPAddress | Select-Object -First 1)
if (-not $lanIp) {
    $lanIp = "AUTO-DETECT-FAILED"
    Write-Warning "Could not auto-detect LAN IP. Check 'ipconfig' manually."
} else {
    Write-Success "LAN IP: $lanIp"
}

$hostname = "instagram-blocker.local"
Write-Info "Android Private DNS hostname: $hostname"
Write-Info "  (Note: Android Private DNS requires a publicly resolvable DoT hostname/TLS cert; use Wi-Fi DNS or hotspot if self-signed DoT is rejected.)"

# ─── Windows Firewall Rules ────────────────────────────────────────────────
Write-Header "Configuring Windows Firewall"

$fwRules = @(
    @{ Name = "IG-DNS-Sinkhole-UDP-53";    Proto = "UDP"; Port = $DnsPort;  Desc = "Instagram DNS Sinkhole (UDP)" },
    @{ Name = "IG-DNS-Sinkhole-TCP-53";    Proto = "TCP"; Port = $DnsPort;  Desc = "Instagram DNS Sinkhole (TCP)" },
    @{ Name = "IG-DNS-Sinkhole-TCP-853";   Proto = "TCP"; Port = $DotPort;  Desc = "Instagram DNS-over-TLS (DoT)" },
    @{ Name = "IG-DNS-Sinkhole-HTTP-8080"; Proto = "TCP"; Port = $HttpPort; Desc = "Instagram DNS Sinkhole Dashboard" }
)

foreach ($rule in $fwRules) {
    $existingRule = Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
    if ($existingRule) {
        Write-Info "Firewall rule '$($rule.Name)' already exists"
    } else {
        New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Protocol $rule.Proto -LocalPort $rule.Port -Action Allow -Profile Any -Description $rule.Desc | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Firewall rule created for $($rule.Proto)/$($rule.Port)"
        } else {
            Write-ErrorMsg "Failed to create firewall rule for $($rule.Proto)/$($rule.Port)"
            exit 1
        }
    }
}

# ─── Register Startup Scheduled Task ──────────────────────────────────────
Write-Header "Registering Startup Task"

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Warning "Task '$TaskName' already exists. Replacing..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { $pythonw = $PythonExe }

$action    = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$scriptPath`"" -WorkingDirectory $SrcDir
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Description $Description -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Write-Success "Scheduled task '$TaskName' registered (runs at startup)"

# ─── Start Task ───────────────────────────────────────────────────────────
Write-Header "Starting DNS Sinkhole"
Start-ScheduledTask -TaskName $TaskName
Start-Sleep 3
$taskInfo = Get-ScheduledTask -TaskName $TaskName
Write-Success "Task state: $($taskInfo.State)"

# ─── Summary ──────────────────────────────────────────────────────────────
Write-Header "SETUP COMPLETE"
Write-Host ""
Write-Host "PHONE CONFIGURATION" -ForegroundColor Yellow
Write-Host "-------------------"
Write-Host ""
Write-Host "Option 1 - Private DNS (Android 9+)" -ForegroundColor Green
Write-Host "  Settings -> Network & internet -> Private DNS -> Private DNS provider hostname"
Write-Host "  Enter: $hostname"
Write-Host ""
Write-Host "Option 2 - Wi-Fi DNS (Static IP)" -ForegroundColor Green
Write-Host "  Wi-Fi settings -> Modify network -> Advanced -> IP settings -> Static"
Write-Host "  DNS 1: $lanIp"
Write-Host "  DNS 2: 1.1.1.1  (fallback when laptop is off)"
Write-Host ""
Write-Host "Option 3 - Hotspot from Laptop (Windows Mobile Hotspot)" -ForegroundColor Green
Write-Host "  Settings -> Network & internet -> Mobile hotspot -> Share my Internet"
Write-Host "  Connect phone to this hotspot -> Uses laptop DNS automatically"
Write-Host ""
Write-Host "VERIFY" -ForegroundColor Yellow
Write-Host "------"
Write-Host "  Dashboard: http://$lanIp`:$HttpPort/status"
Write-Host "  DoT Test:  kdig @$lanIp +tls-ca +tls-host=$hostname instagram.com"
Write-Host "  Expected:  { `"state`": `"LOCKED`" }  (outside 23:30-06:00)"
Write-Host ""
Write-Host "MANAGEMENT" -ForegroundColor Yellow
Write-Host "----------"
Write-Host "  Start:     Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Stop:      Stop-ScheduledTask -TaskName $TaskName"
Write-Host "  Status:    Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Uninstall: .\uninstall.ps1"
Write-Host ""
Write-Host "ANDROID APP ENFORCEMENT (for cellular / away-from-laptop)" -ForegroundColor Magenta
Write-Host "----------------------------------------------------------"
Write-Host "  Run: .\..\android\android_setup.ps1  (from Admin PowerShell with phone via USB/ADB)"
Write-Host ""
Write-Host "NOTE: Laptop DNS only works when phone shares the same network." -ForegroundColor Red
Write-Host "      For cellular/away blocking, use the Android companion."
Write-Host ""