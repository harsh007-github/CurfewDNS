<#
.SYNOPSIS
    Sets up on-device Instagram blocking for Android (works on cellular/away from laptop).

.DESCRIPTION
    This script configures Android-side enforcement using ADB:
    1. Downloads and installs RethinkDNS (open-source, no-root firewall/DNS filter)
    2. Creates and pushes a blocklist covering Instagram/Meta/DoH domains
    3. Writes a reference RethinkDNS config JSON
    4. Creates ADB helper .bat scripts and optional Windows Scheduled Tasks

.REQUIREMENTS
    - Windows 10/11 with ADB in PATH
    - Android device with USB debugging enabled and RSA prompt accepted
    - Administrator privileges

.EXAMPLE
    .\android_setup.ps1

.NOTES
    Run from Admin PowerShell with phone connected via USB (or Wi-Fi ADB).
    RethinkDNS uses Android's VPN API - no root required.
#>

# ─── Configuration ────────────────────────────────────────────────────────
$ScriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot     = Join-Path $ScriptDir "..\.."
$ConfigDir       = Join-Path $ProjectRoot "config"
$ScriptsDir      = $ScriptDir
$AdbExe          = "adb"
$PackageName     = "com.celzero.rethink"
$ApkUrl          = "https://github.com/celzero/rethink-app/releases/download/v0.5.8/rethink-70.apk"
$LocalApk        = Join-Path $ScriptsDir "rethink.apk"

# Time window (must match dns_sinkhole.py: allow 23:30 - 06:00, block 06:00 - 23:30)
$LockStartHour   = 6
$LockStartMin    = 0
$UnlockStartHour = 23
$UnlockStartMin  = 30

# Laptop LAN IP (for reference when on same network)
$LaptopLanIp     = "AUTO"

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

# ─── Check ADB ────────────────────────────────────────────────────────────
Write-Header "Checking ADB Installation"
try {
    $adbVersion = & $AdbExe version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ADB not found" }
    Write-Success "ADB found: $($adbVersion[0])"
} catch {
    Write-ErrorMsg "ADB not found in PATH."
    Write-Info "Install Android Platform Tools: https://developer.android.com/studio/releases/platform-tools"
    Write-Info "Or run: winget install Google.PlatformTools --scope machine"
    exit 1
}

# ─── Check Device Connection ──────────────────────────────────────────────
Write-Header "Checking Device Connection"
$devices = & $AdbExe devices 2>&1
$deviceLines = $devices | Where-Object { $_ -match "^\S+\s+device$" }
if ($deviceLines.Count -eq 0) {
    Write-ErrorMsg "No authorized device found."
    Write-Info "1. Enable USB Debugging: Settings -> Developer options -> USB debugging"
    Write-Info "2. Connect phone via USB"
    Write-Info "3. Accept 'Allow USB debugging?' prompt on phone"
    Write-Info "4. Verify with: adb devices"
    exit 1
}
$deviceId = ($deviceLines[0] -split "\s+")[0]
Write-Success "Device connected: $deviceId"

# ─── Auto-detect Laptop LAN IP ────────────────────────────────────────────
if ($LaptopLanIp -eq "AUTO") {
    $ipConfig = Get-NetIPConfiguration | Where-Object {
        $_.IPv4Address.IPAddress -like "192.168.*" -or
        $_.IPv4Address.IPAddress -like "10.*" -or
        $_.IPv4Address.IPAddress -like "172.16.*"
    }
    $detectedIp = ($ipConfig.IPv4Address.IPAddress | Select-Object -First 1)
    if ($detectedIp) {
        $LaptopLanIp = $detectedIp
        Write-Success "Auto-detected laptop LAN IP: $LaptopLanIp"
    } else {
        $LaptopLanIp = "192.168.1.100"
        Write-Warning "Could not auto-detect LAN IP. Using placeholder: $LaptopLanIp"
    }
}

# ─── Download RethinkDNS APK ──────────────────────────────────────────────
Write-Header "Downloading RethinkDNS APK (v0.5.8)"
if (Test-Path $LocalApk) {
    Write-Info "APK already exists at $LocalApk, skipping download"
} else {
    try {
        # Use curl.exe for reliable download (avoids Invoke-WebRequest TLS issues)
        & curl.exe -L -o $LocalApk $ApkUrl
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Downloaded RethinkDNS APK to $LocalApk"
        } else {
            Write-ErrorMsg "Download failed with curl.exe"
            exit 1
        }
    } catch {
        Write-ErrorMsg "Download error: $_"
        exit 1
    }
}

# ─── Install RethinkDNS ───────────────────────────────────────────────────
Write-Header "Installing RethinkDNS on Device"
$installResult = (& $AdbExe install -r -g $LocalApk 2>&1) -join "`n"
if ($installResult -match "Success") {
    Write-Success "RethinkDNS installed"
} elseif ($installResult -match "INSTALL_FAILED_VERSION_DOWNGRADE") {
    Write-Warning "Newer version already installed"
} else {
    Write-ErrorMsg "Install failed: $installResult"
    exit 1
}

# ─── Grant Permissions ────────────────────────────────────────────────────
Write-Header "Granting Permissions"
& $AdbExe shell pm grant $PackageName android.permission.INTERNET 2>&1 | Out-Null
& $AdbExe shell pm grant $PackageName android.permission.FOREGROUND_SERVICE 2>&1 | Out-Null
& $AdbExe shell pm grant $PackageName android.permission.RECEIVE_BOOT_COMPLETED 2>&1 | Out-Null
& $AdbExe shell pm grant $PackageName android.permission.FOREGROUND_SERVICE_DATA_SYNC 2>&1 | Out-Null
Write-Info "VPN permission requires manual approval on device on first launch."

# ─── Create Blocklist ─────────────────────────────────────────────────────
Write-Header "Creating Instagram/Meta Blocklist"
$blocklistContent = @"
# Instagram Time-Window Blocklist for RethinkDNS
# Generated by android_setup.ps1
# Block window: 06:00 - 23:30 | Allow window: 23:30 - 06:00

# Core Instagram
||instagram.com^
||cdninstagram.com^
||ig.me^

# Facebook/CDN infrastructure used by Instagram
||fbcdn.net^
||fbsbx.com^
||fb.com^
||facebook.com^
||facebook.net^

# Instagram-specific subdomains & services
||instagram.net^
||i.instagram.com^
||graph.instagram.com^
||api.instagram.com^
||www.instagram.com^
||help.instagram.com^
||business.instagram.com^

# Real-time / messaging / push
||edge-chat.instagram.com^
||edge-chat.facebook.com^
||mqtt-mini.facebook.com^
||mqtt.facebook.com^
||push.facebook.com^

# Meta / WhatsApp / Threads crossover domains
||whatsapp.net^
||whatsapp.com^
||threads.net^
||cdn.threads.net^

# DoH endpoints (block to force system/configured DNS)
||dns.google^
||doh.dns.sb^
||cloudflare-dns.com^
||dns.cloudflare.com^
||dns.quad9.net^
||dns.nextdns.io^
||doh.opendns.com^
||doh.cleanbrowsing.org^
||doh.adguard.com^
||dns.adguard.com^
||doh.libredns.gr^
"@

$blocklistPath = Join-Path $ConfigDir "instagram_blocklist.txt"
$blocklistContent | Out-File -FilePath $blocklistPath -Encoding UTF8
Write-Success "Blocklist saved to: $blocklistPath"

# ─── Push Blocklist to Device ─────────────────────────────────────────────
Write-Header "Pushing Blocklist to Device"
$deviceBlocklistPath = "/sdcard/Download/instagram_blocklist.txt"
& $AdbExe push $blocklistPath $deviceBlocklistPath 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Success "Blocklist pushed to device: $deviceBlocklistPath"
} else {
    Write-Warning "Could not push blocklist to /sdcard/Download/. Import manually from PC."
}

# ─── Create RethinkDNS Config (JSON) ──────────────────────────────────────
Write-Header "Creating RethinkDNS Configuration"
$configObj = @{
    version = 1
    dns = @{
        upstream = @(
            @{ type = "dot"; address = "1.1.1.1"; port = 853; hostname = "cloudflare-dns.com" }
            @{ type = "dot"; address = "9.9.9.9"; port = 853; hostname = "dns.quad9.net" }
        )
        fallback = @(
            @{ type = "system" }
        )
        blocklist = @(
            @{ url = "file:///sdcard/Download/instagram_blocklist.txt"; enabled = $true; name = "Instagram Time-Window" }
        )
        allowlist = @()
        hosts = @{}
    }
    firewall = @{
        enabled = $true
        defaultAction = "allow"
        rules = @()
        appRules = @{
            "com.instagram.android" = @{
                wifi = "block_scheduled"
                mobile = "block_scheduled"
                schedule = @{
                    enabled = $true
                    timezone = "local"
                    rules = @(
                        @{ action = "block"; start = "06:00"; end = "23:30"; days = @(1, 2, 3, 4, 5, 6, 7) }
                        @{ action = "allow"; start = "23:30"; end = "06:00"; days = @(1, 2, 3, 4, 5, 6, 7) }
                    )
                }
            }
        }
    }
    vpn = @{
        enabled = $true
        bypassApps = @()
        alwaysOn = $true
        lockdown = $true
    }
    settings = @{
        logLevel = "info"
        notificationLevel = "minimal"
        theme = "system"
    }
}
$configJson = $configObj | ConvertTo-Json -Depth 10

$configPath = Join-Path $ConfigDir "rethink_config.json"
$configJson | Out-File -FilePath $configPath -Encoding UTF8
Write-Success "Config saved to: $configPath"

$deviceConfigPath = "/sdcard/Download/rethink.json"
& $AdbExe push $configPath $deviceConfigPath 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Success "Config pushed to device: $deviceConfigPath"
}

# ─── Create ADB Helper Scripts ────────────────────────────────────────────
Write-Header "Creating ADB Helper Scripts"

$forceStopScript = @"
@echo off
REM Force-stop Instagram app (run at 06:00)
adb shell am force-stop com.instagram.android
adb shell am kill-all
echo Instagram processes killed at %TIME%
"
$forceStopPath = Join-Path $ScriptsDir "android_force_stop_instagram.bat"
$forceStopScript | Out-File -FilePath $forceStopPath -Encoding ASCII
Write-Success "Force-stop script: $forceStopPath"

$enableVpnScript = @"
@echo off
REM Prepare RethinkDNS VPN (run at 06:00)
adb shell cmd connectivity vpn prepare com.celzero.rethink
echo RethinkDNS VPN prepared at %TIME%
"
$enableVpnPath = Join-Path $ScriptsDir "android_enable_vpn.bat"
$enableVpnScript | Out-File -FilePath $enableVpnPath -Encoding ASCII

$disableVpnScript = @"
@echo off
REM Stop RethinkDNS VPN service (run at 23:30)
adb shell am stopservice -n com.celzero.rethink/.core.VpnService
echo RethinkDNS VPN stopped at %TIME%
"
$disableVpnPath = Join-Path $ScriptsDir "android_disable_vpn.bat"
$disableVpnScript | Out-File -FilePath $disableVpnPath -Encoding ASCII

Write-Success "VPN control scripts created"

# ─── Create Private DNS Helper Scripts ────────────────────────────────────
Write-Header "Creating Private DNS Helper Scripts"
Write-Host "Laptop LAN IP detected: $LaptopLanIp (hostname: instagram-blocker.local)"

$privateDnsOn = @"
@echo off
REM Enable Private DNS pointing to hostname
adb shell settings put global private_dns_mode hostname
adb shell settings put global private_dns_specifier instagram-blocker.local
echo Private DNS enabled: instagram-blocker.local
"
$privateDnsOnPath = Join-Path $ScriptsDir "android_private_dns_on.bat"
$privateDnsOn | Out-File -FilePath $privateDnsOnPath -Encoding ASCII

$privateDnsOff = @"
@echo off
REM Disable Private DNS (use system default)
adb shell settings put global private_dns_mode off
echo Private DNS disabled
"
$privateDnsOffPath = Join-Path $ScriptsDir "android_private_dns_off.bat"
$privateDnsOff | Out-File -FilePath $privateDnsOffPath -Encoding ASCII

Write-Success "Private DNS toggle scripts created"

# ─── Create Windows Scheduled Tasks (Optional) ─────────────────────────────
Write-Header "Creating Windows Scheduled Tasks (for ADB automation)"
Write-Host "These tasks run ADB commands at window boundaries if the phone is connected via ADB."

$taskDefs = @(
    @{ Name = "IG-Block-At-0600-Stop"; Time = "06:00"; Script = $forceStopPath },
    @{ Name = "IG-Block-At-0600-VPN";  Time = "06:00"; Script = $enableVpnPath },
    @{ Name = "IG-Unlock-At-2330-VPN"; Time = "23:30"; Script = $disableVpnPath }
)

foreach ($task in $taskDefs) {
    $existing = Get-ScheduledTask -TaskName $task.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Info "Task '$($task.Name)' already exists, skipping"
        continue
    }

    $action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$($task.Script)`""
    $trigger  = New-ScheduledTaskTrigger -Daily -At $task.Time
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    try {
        Register-ScheduledTask -TaskName $task.Name -Action $action -Trigger $trigger -Settings $settings -Description "Instagram DNS Sinkhole: $($task.Name)" -Force | Out-Null
        Write-Success "Created scheduled task: $($task.Name) at $($task.Time)"
    } catch {
        Write-Warning "Could not create task '$($task.Name)': $_"
    }
}

# ─── Summary & Manual Steps ───────────────────────────────────────────────
Write-Header "SETUP COMPLETE - MANUAL STEPS REQUIRED"
Write-Host ""
Write-Host "ON DEVICE (RethinkDNS App):" -ForegroundColor Yellow
Write-Host "1. Open RethinkDNS app on phone and approve the VPN connection prompt."
Write-Host "2. Configure DNS upstream (DoT) and firewall/app rules for Instagram."
Write-Host "3. Enable 'Always-on VPN' and 'Block connections without VPN' in Android VPN settings."
Write-Host "4. On Android 14+, set RethinkDNS battery usage to Unrestricted so it is not killed."
Write-Host ""
Write-Host "FILES CREATED:" -ForegroundColor Yellow
Write-Host "  $blocklistPath"
Write-Host "  $configPath"
Write-Host "  $forceStopPath"
Write-Host "  $enableVpnPath"
Write-Host "  $disableVpnPath"
Write-Host "  $privateDnsOnPath"
Write-Host "  $privateDnsOffPath"
Write-Host ""
Write-Host "TEST COMMANDS:" -ForegroundColor Yellow
Write-Host "  adb shell am force-stop com.instagram.android"
Write-Host "  adb logcat -s RethinkDNS:V *:S"
Write-Host ""