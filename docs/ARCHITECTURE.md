# Instagram Time-Window Blocker — Project Context (Verified State)

## 1. Goal & Hybrid Architecture

**Goal:** Enforce strict daily schedule for Instagram (Android app + mobile web) on Android (tested on OPPO F17 / ColorOS):
- **LOCKED (blocked):** 06:00 – 23:30 local time
- **UNLOCKED (allowed):** 23:30 – 06:00 local time

**Two-Layer Defense-in-Depth:**
- **Layer 1 — Laptop DNS Sinkhole (`src/dns_sinkhole.py` on Windows 11):**
  - UDP/TCP DNS :53, DoT :853, HTTP Dashboard :8080
  - Blocks 30 Instagram/Meta domain suffixes via NXDOMAIN + 0.0.0.0/:: (TTL=30s locked, 300s unlocked)
  - Midnight-crossover logic: `UNLOCK_START=(23,30)`, `UNLOCK_END=(6,0)` → window spans midnight
  - Active when phone on laptop hotspot (default Windows Mobile Hotspot IP: `192.168.137.1`) or home router Wi-Fi (`<YOUR_LAPTOP_LAN_IP>`)
- **Layer 2 — On-Device (RethinkDNS v0.5.8 on Android):**
  - Android VPN API (no root)
  - Firewall: blocks Instagram app UID (stops hardcoded IPs, QUIC/UDP 443, DoH)
  - DNS: built-in "Instagram / Social" blocklist (stops mobile web)
  - Active on cellular, any Wi-Fi, laptop off

## 2. File Inventory (Project Root: `<PROJECT_ROOT>`)

| File | Status | Notes |
|------|--------|-------|
| `src/dns_sinkhole.py` | ✅ Verified | UDP/TCP/DoT + HTTP dashboard, 30 suffixes, TTL tuning |
| `scripts/windows/setup.ps1` | ✅ Fixed | **Must save as UTF-8 with BOM** for PowerShell 5.1; creates 4 firewall rules + startup task via `schtasks` |
| `scripts/windows/uninstall.ps1` | ✅ Verified | Removes task + 4 firewall rules |
| `scripts/android/android_setup.ps1` | ✅ Fixed | **UTF-8 BOM**; corrected `@(1..7)` arrays, `@"` here-strings; downloads `rethink-70.apk` via `curl.exe -L` |
| `artifacts/rethink.apk` | ✅ Verified | RethinkDNS v0.5.8 (`rethink-70.apk` universal) — **gitignored** |
| `scripts/android/android_force_stop_instagram.bat` | ✅ Verified | `adb shell am force-stop com.instagram.android` |
| `scripts/android/android_enable_vpn.bat` | ✅ Verified | `adb shell am startservice -n com.celzero.rethink/.core.VpnService -a android.net.VpnService.START` |
| `scripts/android/android_disable_vpn.bat` | ✅ Verified | `adb shell am stopservice -n com.celzero.rethink/.core.VpnService` |
| `scripts/android/android_private_dns_on.bat` | ⚠️ Unused | Private DNS rejected on Android (see caveat) |
| `scripts/android/android_private_dns_off.bat` | ⚠️ Unused | Private DNS rejected on Android (see caveat) |
| `requirements.txt` | ✅ Verified | `dnslib==0.9.24`, `cryptography>=45.0.7,<47` |
| `README.md` | ✅ Updated | User-facing documentation |
| `context.txt` | ✅ Created | This file (AI resumption context) |
| `config/instagram_blocklist.txt` | ✅ Verified | Hosts-format blocklist for RethinkDNS |
| `config/rethink_config.json` | ✅ Verified | RethinkDNS full config backup |
| `artifacts/dot_cert.pem` | ✅ Verified | Self-signed DoT cert — **gitignored** |
| `artifacts/dot_key.pem` | ✅ Verified | Self-signed DoT private key — **gitignored** |

## 3. Key Technical Fixes & Discoveries

### 3.1 PowerShell Encoding & Syntax (Critical)
- **Problem:** Windows PowerShell 5.1 corrupts `→` to `â†’` and throws `AmpersandNotAllowed` on unquoted `&` in here-strings.
- **Fix:** Save **all `.ps1` files as UTF-8 with BOM** (Notepad++ → Encoding → UTF-8-BOM; VS Code → Save with Encoding → UTF-8 with BOM).
- **Syntax fixes in `scripts/android/android_setup.ps1`:**
  - Day arrays: `$days = @(1,2,3,4,5,6,7)` or `@(1..7)` — not `[1,2,3,4,5,6,7]`
  - Here-strings: Use `@"` ... `"@` with no leading/trailing spaces on delimiters
  - ADB install: `winget install --id Google.PlatformTools -e --scope machine` (user-scope fails in Admin shell); then `$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")`

### 3.2 Network & IP Configuration (Verified)
```cmd
ipconfig | findstr /i "adapter IPv4"
```
**Active IPv4s on laptop:**
| Adapter | IPv4 | Use Case |
|---------|------|----------|
| `Local Area Connection* 4` (Windows Mobile Hotspot) | `192.168.137.1` | Phone on laptop hotspot (Windows default) |
| `Wireless LAN adapter Wi-Fi` (Home router) | `<YOUR_LAPTOP_LAN_IP>` | Phone on home Wi-Fi (user-specific) |

### 3.3 Android Private DNS — REJECTED on Android (tested on OPPO F17 / ColorOS)
- **Tested:** `Settings → Connection & sharing → Private DNS → Provider hostname → instagram-blocker.local`
- **Result:** *"Failed to connect to private dns server"*
- **Root causes:** (1) `.local` TLD not accepted; (2) Self-signed cert not trusted.
- **Workaround:** **Private DNS = Off/Auto**. Use **Wi-Fi Static DNS** instead (both DNS 1 & 2 = laptop IP).

### 3.4 Wi-Fi Static DNS (Verified Working)
```
Settings → Wi-Fi → ⓘ (connected SSID) → IP settings → Static
→ DNS 1: <LAPTOP_IP>   (192.168.137.1 on hotspot, <YOUR_LAPTOP_LAN_IP> on router)
→ DNS 2: <LAPTOP_IP>   (MUST BE SAME — never 1.1.1.1)
```
Apply to **both** laptop hotspot SSID and home router SSID.

### 3.5 RethinkDNS v0.5.8 UI Behavior (Corrected from Earlier Assumptions)
| Feature | v0.5.8 Reality | Earlier (Wrong) Assumption |
|---------|----------------|---------------------------|
| Custom blocklist import | **No `.txt` file import UI** | Thought `instagram_blocklist.txt` could be imported |
| App blocking | `Configure → Apps → Instagram` → tap toggle/button to **Block** | Thought per-app scheduling existed |
| DNS blocklists | `Configure → DNS → On-device blocklists` → enable **Instagram / Social** (built-in) | Thought custom lists required |
| VPN control | **Stop** = fully stops VPN (allows traffic); **Pause** = keeps app blocks active | Thought Pause allowed all traffic |

**Verified Working Config on Android:**
1. `Configure → Apps → Instagram` → **Block** (stops app UID traffic)
2. `Configure → DNS → On-device blocklists` → **Instagram / Social = Enabled** (stops web)
3. `Configure → DNS → Upstream` → `DoT` → `Cloudflare` (optional)
4. `Configure → Logging → Log Level` → `None` (battery saver)

### 3.6 ColorOS Anti-Kill Settings (All Required on OPPO F17)
| Setting | Path | Value |
|---------|------|-------|
| Always-on VPN | `Settings → Connection & sharing → VPN → RethinkDNS (ⓘ)` | **ON** |
| Block connections without VPN | Same | **OFF** (allows internet during 23:30–06:00 unlock) |
| Allow background activity | `Settings → Apps → App management → RethinkDNS → Battery usage` | **ON** |
| Allow auto-launch | Same | **ON** |
| Optimize standby / Sleep standby optimization | `Settings → Battery → More battery settings` | **OFF** |
| Lock in Recent Apps | Recent Apps switcher → RethinkDNS icon → padlock | **Locked** |

## 4. Daily Operation & Battery

| Window | Laptop DNS | RethinkDNS (Phone) | Action |
|--------|------------|-------------------|--------|
| **23:30 – 06:00 (UNLOCK)** | Forwards (TTL=300s) | **Stop** (tap Stop in app) | Instagram works |
| **06:00 – 23:30 on Hotspot/Router** | Sinkholes (TTL=30s) | Optional (Stop/Start) | Laptop enforces |
| **06:00 – 23:30 on Mobile Data** | N/A | **Start** (tap Start in app) | Phone enforces |

**Battery (OPPO F17, v0.5.8):**
- Log Level `None`, Instagram closed from Recents when locked: **~1–3%/day**
- Log Level `Info`, Instagram in Recents: **~2–5%/day**

**RethinkDNS does NOT need to run 24/7** — only when on-device enforcement needed.

## 5. Verified Commands

### Laptop (Admin PowerShell)
```powershell
# Setup (run from project root)
cd "<PROJECT_ROOT>"
.\scripts\windows\setup.ps1

# Status
Get-ScheduledTask -TaskName "IG-DNS-Sinkhole"
curl http://localhost:8080/status

# Debug (foreground)
python src/dns_sinkhole.py

# Uninstall
.\scripts\windows\uninstall.ps1
```

### Phone (ADB)
```bash
adb devices
adb shell am force-stop com.instagram.android
adb shell am startservice -n com.celzero.rethink/.core.VpnService -a android.net.VpnService.START
adb shell am stopservice -n com.celzero.rethink/.core.VpnService
adb logcat -s RethinkDNS:V *:S
```

### MacroDroid Automation (if used)
- Time 06:00 → `adb shell am force-stop com.instagram.android` + `adb shell am startservice -n com.celzero.rethink/.core.VpnService -a android.net.VpnService.START`
- Time 23:30 → `adb shell am stopservice -n com.celzero.rethink/.core.VpnService`

## 6. Known Caveats & Non-Issues

| Issue | Status | Resolution |
|-------|--------|------------|
| Private DNS `.local` hostname | ❌ Rejected | Use Wi-Fi Static DNS (both = laptop IP) |
| Self-signed DoT cert | ❌ Rejected | Use Wi-Fi Static DNS |
| RethinkDNS Pause mode | ⚠️ Still blocks apps | Use **Stop** (not Pause) during unlock |
| Windows startup task vs service | ✅ Task used | `schtasks` runs `pythonw.exe` hidden at logon |
| Meta ASN IP ranges change | ⚠️ Quarterly update | Optional firewall rules in README |
| RethinkDNS killed by ColorOS | ✅ Fixed | All 6 anti-kill settings applied |

## 7. Resume Instructions for Future AI Session

1. **Read `context.txt` and `README.md`** — full state captured here.
2. **Verify laptop IPs:** `ipconfig | findstr /i "adapter IPv4"` — expect `192.168.137.1` (hotspot) and `<YOUR_LAPTOP_LAN_IP>` (router).
3. **Check task:** `Get-ScheduledTask -TaskName "IG-DNS-Sinkhole"` → should be `Ready` or `Running`.
4. **Test dashboard:** `curl http://localhost:8080/status` → should show current state + next flip.
5. **Phone Wi-Fi Static DNS:** Verify both DNS = correct laptop IP for current network.
6. **RethinkDNS:** Open app → verify VPN permission granted, Instagram blocked in Apps, Instagram/Social enabled in Blocklists, anti-kill settings intact.
7. **Do NOT re-run `setup.ps1` or `android_setup.ps1`** unless files changed — they are idempotent but may reset configs.
8. **Current time window:** Locked 06:00–23:30, Unlocked 23:30–06:00 (midnight crossover handled in `src/dns_sinkhole.py` lines 156-211).

## 8. Next Steps (If Needed)
- Add Meta ASN firewall rules quarterly (see README Advanced section).
- Monitor RethinkDNS updates — v0.5.9+ may add custom blocklist import.
- Consider Termux + cron for fully on-device scheduling (no ADB dependency).