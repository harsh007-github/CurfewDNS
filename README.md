# Instagram Time-Window Blocker — Hybrid (Laptop DNS + Android On-Device)

Enforces a strict daily schedule for **Instagram (Android app + mobile web)**:

| Window | Local Time | State |
|--------|------------|-------|
| **LOCKED**   | 06:00 – 23:30 | **BLOCKED** |
| **UNLOCKED** | 23:30 – 06:00 | **ALLOWED** |

---

## Hybrid Two-Layer Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1 — Laptop-Hosted DNS Sinkhole (src/dns_sinkhole.py)     │
│ • Runs on Windows 11 laptop (Python 3.10+)                     │
│ • UDP/TCP DNS on :53  • DoT on :853  • HTTP Dashboard on :8080 │
│ • Blocks Instagram/Meta domains via NXDOMAIN + 0.0.0.0/::      │
│ • TTL=30s (locked) / 300s (unlocked) for instant transitions  │
│ • Works when phone is on laptop's Wi-Fi Hotspot or home router │
└─────────────────────────────────────────────────────────────────┘
                              ↑
                    Phone on same LAN
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Layer 2 — On-Device Enforcement (RethinkDNS v0.5.8 on Android) │
│ • Android VPN API (no root)                                    │
│ • Firewall: blocks Instagram app UID (stops hardcoded IPs/QUIC)│
│ • DNS: built-in "Instagram / Social" blocklist (stops web)     │
│ • Works on cellular, any Wi-Fi, laptop off                     │
└─────────────────────────────────────────────────────────────────┘
```

**Why both?**  
- Laptop DNS alone fails against: DoH, hardcoded IPs, QUIC/UDP 443, cellular fallback.  
- On-device alone requires: app install, VPN permission, battery optimizations.  
- **Together** = defense in depth. Laptop handles bulk filtering + dashboard; device handles bypasses + away-from-home.

---

## Project Structure

```
<PROJECT_ROOT>/
├── README.md
├── context.txt
├── requirements.txt
├── .gitignore
├── src/
│   └── dns_sinkhole.py              # Main DNS server (UDP/TCP/DoT + HTTP dashboard)
├── scripts/
│   ├── windows/
│   │   ├── setup.ps1                # Windows 11 install (firewall, certs, startup task)
│   │   └── uninstall.ps1            # Clean removal (task + firewall rules)
│   └── android/
│       ├── android_setup.ps1        # ADB setup: installs RethinkDNS, pushes configs
│       ├── android_force_stop_instagram.bat
│       ├── android_enable_vpn.bat
│       ├── android_disable_vpn.bat
│       ├── android_private_dns_on.bat
│       └── android_private_dns_off.bat
├── config/
│   ├── instagram_blocklist.txt      # Hosts-format blocklist for RethinkDNS
│   └── rethink_config.json          # RethinkDNS full config backup
└── artifacts/        (gitignored: dot_cert.pem, dot_key.pem, rethink.apk)
```

---

## Quick Start — Windows 11 (Admin PowerShell)

```powershell
# 1. Open PowerShell as Administrator
# 2. Navigate to project folder
cd "<PROJECT_ROOT>"

# 3. Run Windows setup (from scripts/windows/)
.\scripts\windows\setup.ps1

# 4. Verify task is running
Get-ScheduledTask -TaskName "IG-DNS-Sinkhole"
# State should be "Running" or "Ready"

# 5. Check dashboard (from laptop)
curl http://localhost:8080/status
# Expected outside 23:30–06:00: {"state":"LOCKED","instagram_access":"blocked",...}
```

**`scripts/windows/setup.ps1` does:**
- Installs `dnslib` + `cryptography` via pip (from `requirements.txt`)
- Generates self-signed TLS cert (`artifacts/dot_cert.pem`, `artifacts/dot_key.pem`) for DoT
- Creates 4 Windows Firewall rules: UDP/53, TCP/53, TCP/853, TCP/8080
- Registers a **background startup task** via `schtasks` so `src/dns_sinkhole.py` runs at boot (SYSTEM, hidden)
- Prints laptop LAN IPs for phone configuration

---

## Android — Verified Setup (tested on OPPO F17 / ColorOS)

### A. Laptop IP Addresses (from `ipconfig | findstr /i "adapter IPv4"`)

| Interface | IPv4 | Use Case |
|-----------|------|----------|
| **Windows Mobile Hotspot** (`Local Area Connection* 4`) | `192.168.137.1` | Phone connected to laptop hotspot (Windows default gateway) |
| **Home Wi-Fi Router** (`Wireless LAN adapter Wi-Fi`) | `<YOUR_LAPTOP_LAN_IP>` | Phone on home router Wi-Fi (user-specific) |

> **Use the IP matching the network the phone is currently on.**

### B. Phone Wi-Fi Static DNS (NOT Private DNS)

**Android Private DNS REJECTS:**
- `.local` hostnames (`instagram-blocker.local`) → *"Failed to connect to private dns server"*
- Self-signed certificates (no trust anchor)

**Correct Configuration:**
```
Settings → Wi-Fi → ⓘ (connected network) → IP settings → Static
→ DNS 1: <LAPTOP_IP>   (192.168.137.1 on hotspot, <YOUR_LAPTOP_LAN_IP> on router)
→ DNS 2: <LAPTOP_IP>   (SAME IP — never 1.1.1.1, else it bypasses sinkhole)
```

Do this for **both** the laptop hotspot SSID and your home router SSID.

### C. RethinkDNS v0.5.8 Setup (On-Device Enforcement)

**Install via ADB:**
```powershell
# Phone via USB, USB Debugging ON, ADB authorized
.\scripts\android\android_setup.ps1
```
This downloads `rethink-70.apk` (v0.5.8 universal) to `scripts/android/`, installs it, pushes blocklist/config, and creates helper `.bat` scripts.

**Configure RethinkDNS App (v0.5.8 UI — no `.txt` blocklist import):**

1. **Open RethinkDNS** → Grant **VPN permission** (allow always)
2. **Firewall — Block Instagram App UID (stops hardcoded IPs/QUIC):**
   - `Configure` → `Apps` → find **Instagram** → tap its **toggle/button** to **Block**
   - This blocks *all* network traffic from the Instagram app UID
3. **DNS — Block Instagram Web (mobile browser):**
   - `Configure` → `DNS` → `On-device blocklists`
   - Enable **Instagram / Social** (built-in list blocks `instagram.com`, `cdninstagram.com`, etc.)
4. **Upstream DNS (optional but recommended):**
   - `Configure` → `DNS` → `Upstream` → `DoT` → `Cloudflare` or `Quad9`
5. **Logging (battery saver):**
   - `Configure` → `Logging` → `Log Level` → `None` or `Error`

### D. ColorOS Anti-Kill (Critical — without these RethinkDNS gets killed)

| Setting | Path | Value |
|---------|------|-------|
| **Always-on VPN** | `Settings → Connection & sharing → VPN → RethinkDNS (ⓘ)` | **ON** |
| **Block connections without VPN** | Same screen | **OFF** (so internet works during 23:30–06:00 unlock when VPN is stopped) |
| **Allow background activity** | `Settings → Apps → App management → RethinkDNS → Battery usage` | **ON** |
| **Allow auto-launch** | Same screen | **ON** |
| **Optimize standby / Sleep standby optimization** | `Settings → Battery → More battery settings` | **OFF** |
| **Lock in Recent Apps** | Recent Apps switcher → tap RethinkDNS icon → **Lock (padlock)** | **Locked** |

---

## Daily Operation Workflow

| Time | Action | Layer 1 (Laptop) | Layer 2 (Phone) |
|------|--------|------------------|-----------------|
| **23:30** | **UNLOCK starts** | Auto-forwards Instagram domains (TTL=300s) | **Stop RethinkDNS** (tap Stop in app) — *Pause still blocks apps blocked in Configure→Apps* |
| **06:00** | **LOCK starts** | Auto-sinkholes Instagram domains (TTL=30s) | **Start RethinkDNS** (tap Start in app) |

### Automation Options (pick one)

**Option 1: Manual (simplest, most reliable)**
- 23:30 → Open RethinkDNS → Tap **Stop**
- 06:00 → Open RethinkDNS → Tap **Start**

**Option 2: MacroDroid (free, no root)**
- Trigger: **Time 23:30** → Action: `Run shell command: adb shell am stopservice -n com.celzero.rethink/.core.VpnService`
- Trigger: **Time 06:00** → Action: `Run shell command: adb shell am force-stop com.instagram.android` + `adb shell am startservice -n com.celzero.rethink/.core.VpnService -a android.net.VpnService.START`
- Trigger: **Wi-Fi Connected (Home/Hotspot)** → Optional: set Static DNS via MacroDroid (requires root for DNS change) — skip, use manual static DNS above

**Option 3: Windows Task Scheduler (only when phone on USB/Wi-Fi ADB)**
- Created by `scripts/android/android_setup.ps1`: tasks `IG-Block-At-0600-Stop`, `IG-Block-At-0600-VPN`, `IG-Unlock-At-2330-VPN`
- Run `adb devices` to verify connection before relying on this

---

## Management Commands

### Laptop (Windows 11)

```powershell
# Check status
Get-ScheduledTask -TaskName "IG-DNS-Sinkhole"

# Start/stop background task
Start-ScheduledTask -TaskName "IG-DNS-Sinkhole"
Stop-ScheduledTask -TaskName "IG-DNS-Sinkhole"

# View logs (stdout/stderr from python)
# Run manually for debugging:
python src/dns_sinkhole.py

# Dashboard
curl http://localhost:8080/status
curl http://localhost:8080/metrics
curl http://localhost:8080/config

# Uninstall
.\scripts\windows\uninstall.ps1
```

### Phone (ADB)

```bash
# Verify connection
adb devices

# Force-stop Instagram (run at 06:00)
adb shell am force-stop com.instagram.android

# Enable RethinkDNS VPN (run at 06:00)
adb shell am startservice -n com.celzero.rethink/.core.VpnService -a android.net.VpnService.START

# Disable RethinkDNS VPN (run at 23:30)
adb shell am stopservice -n com.celzero.rethink/.core.VpnService

# Check RethinkDNS logs
adb logcat -s RethinkDNS:V *:S
```

---

## Battery Impact (tested on OPPO F17, RethinkDNS v0.5.8)

| Mode | Est. Daily Drain |
|------|------------------|
| DNS + Firewall (Log Level: None/Error, Instagram closed from Recents when locked) | **1%–3%** |
| DNS + Firewall (Log Level: Info, Instagram in Recents) | **2%–5%** |

**Tips:**
- Set `Log Level = None` in RethinkDNS `Configure → Logging`
- Swipe Instagram **out of Recent Apps** when locked (06:00–23:30)
- RethinkDNS **does not need to run 24/7** — only when you need on-device enforcement

---

## Troubleshooting

### "Instagram still works at 06:00 on hotspot"
- Verify laptop DNS sinkhole is running: `Get-ScheduledTask -TaskName "IG-DNS-Sinkhole"` → State = `Running` or `Ready`
- Verify phone Wi-Fi Static DNS = **both** set to laptop hotspot IP (`192.168.137.1`)
- Run `curl http://192.168.137.1:8080/status` from phone browser — should show `{"state":"LOCKED",...}`

### "Instagram works on mobile data"
- RethinkDNS must be **Started** (not Paused, not Stopped)
- Check `Configure → Apps → Instagram` shows **Blocked**
- Check `Configure → DNS → On-device blocklists → Instagram / Social` = **Enabled**
- Verify Always-on VPN = ON, Block without VPN = OFF

### "RethinkDNS keeps stopping"
- Re-apply all ColorOS anti-kill settings (Section D above)
- Lock in Recent Apps
- Disable battery optimization for RethinkDNS
- Check `adb logcat -s RethinkDNS:V *:S` for crash reason

### "Dashboard not accessible from phone"
- Windows Firewall: `IG-DNS-Sinkhole-HTTP-8080` rule must exist (created by `setup.ps1`)
- Phone and laptop on same network (hotspot or router)
- Try `http://<LAPTOP_IP>:8080/status` in phone browser

### "Private DNS says 'Failed to connect'"
- **Expected** — Android Private DNS rejects `.local` and self-signed certs
- **Fix:** Use Wi-Fi Static DNS (both DNS 1 & 2 = laptop IP) instead

---

## Advanced: Block Meta ASN Ranges at Windows Firewall (Optional)

For extra laptop-side coverage against hardcoded IPs:

```powershell
# Run once in Admin PowerShell — update ranges quarterly from https://bgp.he.net/AS32934
$metaRanges = @(
    "157.240.0.0/16", "179.60.192.0/20", "31.13.64.0/18", "66.220.144.0/20",
    "69.63.176.0/20", "69.171.224.0/19", "102.132.96.0/20", "103.4.96.0/22",
    "129.134.0.0/16", "173.252.64.0/18", "185.60.216.0/22"
)
foreach ($range in $metaRanges) {
    New-NetFirewallRule -DisplayName "IG-Block-Meta-$range" -Direction Outbound -RemoteAddress $range -Protocol Any -Action Block -Profile Any -Description "Block Meta/Instagram ASN range"
}
```

---

## Uninstall

### Laptop
```powershell
.\scripts\windows\uninstall.ps1
```

### Phone
- Uninstall RethinkDNS app
- `Settings → VPN → Delete RethinkDNS profile`
- Remove MacroDroid profiles if created
- Reset Wi-Fi DNS to Automatic (DHCP)

---

## License

MIT — Use freely, modify, distribute.