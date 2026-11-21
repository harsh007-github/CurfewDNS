# CurfewDNS — Technical Architecture & Design Notes

## 1. Goal & Hybrid Architecture

**Goal:** Enforce a strict daily curfew schedule for distracting apps and domains (initially configured for **Instagram** across the Android app and mobile web; tested on OPPO F17 / ColorOS):
- **LOCKED (blocked):** 06:00 – 23:30 local time
- **UNLOCKED (allowed):** 23:30 – 06:00 local time

**Two-Layer Defense-in-Depth:**
- **Layer 1 — Laptop DNS Sinkhole (`src/dns_sinkhole.py` on Windows 11):**
  - UDP/TCP DNS `:53`, DNS-over-TLS (DoT) `:853`, HTTP Dashboard `:8080`
  - Blocks 30 Instagram/Meta domain suffixes via `NXDOMAIN` + `0.0.0.0`/`::`
  - Dynamic TTL switching (`TTL=10s` near curfew transitions, `TTL=30s` when locked, `TTL=300s` when unlocked) so client caches flush cleanly on state changes
  - Midnight-crossover logic: `UNLOCK_START=(23,30)`, `UNLOCK_END=(6,0)` -> window spans midnight
  - Active when phone is on laptop hotspot (default Windows Mobile Hotspot IP: `192.168.137.1`) or home router Wi-Fi (`<YOUR_LAPTOP_LAN_IP>`)
- **Layer 2 — On-Device Enforcement (RethinkDNS v0.5.8 on Android):**
  - Android VPN API (no root)
  - Firewall: blocks target app UID (stops hardcoded IPs, QUIC/UDP 443, DoH)
  - DNS: built-in "Instagram / Social" blocklist (stops mobile web)
  - Active on cellular, any Wi-Fi, laptop off

## 2. File Inventory (Project Root: `<PROJECT_ROOT>`)

| File | Status | Notes |
|------|--------|-------|
| `src/dns_sinkhole.py` | Verified | UDP/TCP/DoT + HTTP dashboard, 30 suffixes, dynamic TTL & midnight crossover |
| `scripts/windows/setup.ps1` | Fixed | **Must save as UTF-8 with BOM**; resolves paths via `$PSScriptRoot`; creates 4 firewall rules + `IG-DNS-Sinkhole` task |
| `scripts/windows/uninstall.ps1` | Verified | Removes `IG-DNS-Sinkhole` task + 4 firewall rules |
| `scripts/android/android_setup.ps1` | Fixed | **UTF-8 BOM**; corrected `@(1..7)` arrays, `@"` here-strings; downloads `rethink-70.apk` via `curl.exe -L` |
| `artifacts/rethink.apk` | Verified | RethinkDNS v0.5.8 (`rethink-70.apk` universal) — **gitignored** |
| `scripts/android/android_force_stop_instagram.bat` | Verified | `adb shell am force-stop com.instagram.android` |
| `scripts/android/android_enable_vpn.bat` | Verified | `adb shell am startservice -n com.celzero.rethink/.core.VpnService -a android.net.VpnService.START` |
| `scripts/android/android_disable_vpn.bat` | Verified | `adb shell am stopservice -n com.celzero.rethink/.core.VpnService` |
| `scripts/android/android_private_dns_on.bat` | Unused | Private DNS rejected on Android (see Section 3.3) |
| `scripts/android/android_private_dns_off.bat` | Unused | Private DNS rejected on Android (see Section 3.3) |
| `requirements.txt` | Verified | `dnslib==0.9.24`, `cryptography>=45.0.7,<47` |
| `README.md` | Updated | User-facing setup, usage, and troubleshooting guide |
| `docs/ARCHITECTURE.md` | Updated | Technical architecture, design decisions, and operational notes |
| `config/instagram_blocklist.txt` | Verified | Domain blocklist reference for RethinkDNS |
| `config/rethink_config.json` | Verified | RethinkDNS full config backup |
| `artifacts/dot_cert.pem` | Verified | Self-signed DoT cert — **gitignored** (never commit keys/certs) |
| `artifacts/dot_key.pem` | Verified | Self-signed DoT private key — **gitignored** (never commit keys/certs) |

## 3. Key Technical Fixes & Discoveries

### 3.1 PowerShell Encoding & Syntax (Critical)
- **Problem:** Windows PowerShell 5.1 corrupts non-ASCII characters if encoding is mismatched and throws `AmpersandNotAllowed` on unquoted `&` in here-strings.
- **Fix:** Save **all `.ps1` files as UTF-8 with BOM** and resolve project/script paths dynamically using `$PSScriptRoot`.
- **Syntax fixes in `scripts/android/android_setup.ps1`:**
  - Day arrays: `$days = @(1,2,3,4,5,6,7)` or `@(1..7)` — not `[1,2,3,4,5,6,7]`
  - Here-strings: Use `@"` ... `"@` with no leading/trailing spaces on delimiters
  - ADB install: `winget install --id Google.PlatformTools -e --scope machine` (user-scope fails in Admin shell); then `$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")`

### 3.2 Network & IP Configuration (Verified)
```cmd
ipconfig | findstr /i "adapter IPv4"