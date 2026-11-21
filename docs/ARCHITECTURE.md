
# CurfewDNS — Technical Architecture & Design Notes

## 1. Project Overview

**CurfewDNS** is a hybrid, cross-device network enforcement system designed to enforce a strict daily curfew on distracting applications and websites.

The initial implementation targets **Instagram**, covering both its native Android application and mobile web access.

The system combines:
- A Windows 11 laptop-hosted DNS sinkhole for network-wide, time-scheduled domain blocking.
- An Android on-device VPN firewall for application-level enforcement and protection against DNS-based bypasses.

### 1.1 Curfew Schedule

The system operates on the device's local time.

| State | Time Window | Behavior |
|---|---|---|
| LOCKED | 06:00 – 23:30 | Instagram and configured domains blocked |
| UNLOCKED | 23:30 – 06:00 | Normal DNS resolution permitted |

The unlocked window crosses midnight.

The schedule is represented internally as:

```python
UNLOCK_START = (23, 30)
UNLOCK_END = (6, 0)
```

The system must correctly handle midnight crossover when determining the active enforcement state.

---

## 2. Hybrid Architecture & Threat Model

### 2.1 Why Two Enforcement Layers Are Necessary

A DNS-only solution is insufficient for reliably blocking modern native applications. Similarly, an on-device VPN firewall alone does not provide the required automated, centralized daily scheduling.

CurfewDNS addresses these limitations through two complementary enforcement layers.

### Layer 1 — Windows DNS Sinkhole

**Component:** `src/dns_sinkhole.py`

The Windows laptop operates as a network-level DNS sinkhole.

Responsibilities:
- Intercepts standard DNS requests.
- Blocks configured Instagram/Meta domain suffixes during LOCKED hours.
- Forwards DNS requests upstream during UNLOCKED hours.
- Dynamically adjusts DNS TTL values around schedule transitions.
- Provides DNS-over-TLS support.
- Exposes an HTTP dashboard for monitoring the current curfew state.

**Limitation:** DNS filtering alone can be bypassed by applications that:
- Aggressively cache resolved A/AAAA records.
- Use hardcoded fallback IP addresses.
- Establish connections through HTTP/3 (QUIC) over UDP.
- Use DNS-over-HTTPS (DoH) or alternative DNS resolvers.
- Reuse existing connections established before a schedule transition.

Consequently, DNS filtering primarily provides domain-level enforcement rather than complete application-level isolation.

### Layer 2 — Android On-Device Enforcement

**Component:** RethinkDNS v0.5.8

RethinkDNS uses Android's `VpnService` API to provide rootless, on-device network filtering.

Responsibilities:
- Applies firewall rules at the application UID level.
- Blocks network access for the configured target application.
- Prevents the application from bypassing domain restrictions through hardcoded IP addresses.
- Restricts alternative network paths, including QUIC/UDP traffic, according to the configured firewall rules.
- Uses its built-in Instagram / Social DNS blocklists to restrict mobile browser access.

**Limitation:** RethinkDNS v0.5.8 does not independently synchronize its firewall state with the laptop's daily curfew schedule.

The laptop-hosted sinkhole provides automated schedule enforcement for clients using it as their DNS resolver. Android UID-level firewall rules remain governed by the locally configured RethinkDNS settings.

### 2.2 Defense-in-Depth Model

| Threat / Bypass Vector | DNS Sinkhole | Android VPN |
|---|---|---|
| Standard DNS resolution | Blocked during LOCKED | Blocked through configured blocklists |
| Cached DNS records | Mitigated through dynamic TTL | Not dependent on DNS cache |
| Hardcoded IP addresses | Not reliably blocked | Blocked through application firewall |
| QUIC / UDP 443 | Not reliably blocked | Restricted through configured UID firewall rules |
| DNS-over-HTTPS | Not inherently blocked | Restricted through configured application rules |
| Mobile browser access | Blocked when using laptop DNS | Restricted through social DNS blocklists |
| Cellular connectivity | Not available | Local enforcement remains active |
| Automated daily scheduling | Yes | No independent schedule synchronization |

**Design principle:** The DNS sinkhole provides centralized, schedule-aware domain filtering, while RethinkDNS provides application-level enforcement independent of the current network.

Neither layer should be treated as a complete substitute for the other.

---

## 3. Layer 1 — Windows 11 DNS Sinkhole

### 3.1 Core Implementation

**Source:** `src/dns_sinkhole.py`

The DNS sinkhole is implemented as a Python daemon targeting Python 3.10+.

It exposes three network services:

| Protocol | Port | Purpose |
|---|---:|---|
| UDP | 53 | Standard DNS queries |
| TCP | 53 | Standard DNS over TCP |
| TCP | 853 | DNS-over-TLS (DoT) |
| TCP | 8080 | HTTP status dashboard |

### 3.2 DNS Enforcement Behavior

During LOCKED hours:

1. Incoming DNS queries are inspected.
2. Requested domains are matched against the configured Instagram/Meta domain suffixes.
3. Matching requests receive a negative DNS response (`NXDOMAIN`) or the configured sinkhole address response for A/AAAA records (`0.0.0.0` / `::`), depending on the response implementation.
4. Non-blocklisted domains continue through the configured upstream resolution path.

During UNLOCKED hours:

1. DNS requests are forwarded to the configured upstream resolver.
2. Normal DNS resolution is restored.
3. Responses are cached according to the active TTL policy.

The initial blocklist contains **30 Instagram/Meta domain suffixes**.

The reference domain list is maintained in:

`config/instagram_blocklist.txt`

### 3.3 DNS-over-TLS (DoT)

CurfewDNS additionally exposes DNS-over-TLS on TCP port `853`.

Implementation details:
- Uses locally generated self-signed TLS certificates.
- Certificate and private key files are stored in `artifacts/`.
- The DoT listener provides encrypted DNS transport between compatible clients and the laptop-hosted resolver.

Certificate files:

```text
artifacts/
├── dot_cert.pem
└── dot_key.pem
```

**Important:** Clients must explicitly support and trust the configured DoT certificate. A self-signed certificate is not automatically trusted by Android or other operating systems.

The certificate and private key must never be committed to Git.

### 3.4 Three-Tier Dynamic TTL Cache-Flush Strategy

One of the most important architectural decisions is dynamically adjusting DNS TTL values based on the curfew schedule.

This minimizes the time clients retain stale DNS responses when the enforcement state changes.

| Schedule Condition | TTL | Purpose |
|---|---:|---|
| Within 10 minutes of 06:00 or 23:30 | 10 seconds | Accelerate cache expiration around transitions |
| LOCKED hours outside transition windows | 30 seconds | Maintain relatively frequent DNS revalidation |
| UNLOCKED hours outside transition windows | 300 seconds | Reduce unnecessary upstream DNS queries |

**Transition boundaries:**
- 06:00 — UNLOCKED → LOCKED
- 23:30 — LOCKED → UNLOCKED

The 10-second transition TTL is particularly important because operating systems and browsers may otherwise retain DNS responses beyond the intended schedule boundary.

**Important distinction:** TTL controls the lifetime of DNS cache entries. It does not forcibly flush existing connections, application-level caches, hardcoded IP addresses, or independently managed DNS caches.

This is why the Android enforcement layer remains necessary.

### 3.5 HTTP Status Dashboard

**Endpoint:** `http://<LAPTOP_IP>:8080`

The lightweight HTTP dashboard exposes operational information, including:

- Current curfew state (`LOCKED` / `UNLOCKED`).
- Active DNS TTL.
- DNS query statistics.
- Current enforcement status.

The dashboard provides a convenient way to verify that the sinkhole daemon is running and responding.

It is intended for local network monitoring, not public internet exposure.

### 3.6 Windows Automation

The sinkhole is deployed through Windows Task Scheduler.

**Scheduled task name:**

`IG-DNS-Sinkhole`

**Setup script:**

`scripts/windows/setup.ps1`

The setup script:
- Configures the required Windows Firewall rules.
- Creates four firewall rules for the sinkhole services.
- Registers the `IG-DNS-Sinkhole` scheduled task.
- Resolves project paths dynamically using `$PSScriptRoot`.
- Configures the daemon for automated execution.

The uninstall script:

`scripts/windows/uninstall.ps1`

removes:
- The `IG-DNS-Sinkhole` scheduled task.
- All four firewall rules created by the setup script.

**PowerShell compatibility requirement:**

All `.ps1` scripts must be saved using **UTF-8 with BOM** to maintain compatibility with Windows PowerShell 5.1.

Dynamic path resolution avoids hardcoding machine-specific project directories.

---

## 4. Layer 2 — Android On-Device Enforcement

### 4.1 Implementation

**Application:** RethinkDNS v0.5.8

**Android package:** `com.celzero.rethink`

**Integration:** `scripts/android/`

RethinkDNS uses Android's native VPN service infrastructure to intercept and filter network traffic without requiring root access.

The implementation uses two complementary filtering mechanisms.

### 4.2 Application-Level Firewall

The firewall targets the Instagram application's Android UID.

This is important because application-level enforcement does not depend exclusively on DNS resolution.

It helps prevent:
- Connections to previously resolved IP addresses.
- Connections through hardcoded fallback IP addresses.
- Application traffic using alternative transport protocols.
- DNS-based restrictions being bypassed by the native application.

The target application's firewall configuration is maintained within RethinkDNS.

### 4.3 DNS-Based Social Web Filtering

RethinkDNS also provides built-in DNS blocklists.

The configured:

`Instagram / Social`

blocklist restricts access to Instagram and associated social domains through mobile browsers.

This complements the laptop-hosted DNS sinkhole, particularly when the Android device is not connected to the laptop's network.

### 4.4 Android VPN Service Management

The following batch scripts automate common RethinkDNS operations through ADB.

| Script | Operation |
|---|---|
| `android_force_stop_instagram.bat` | Force-stops Instagram |
| `android_enable_vpn.bat` | Starts the RethinkDNS VPN service |
| `android_disable_vpn.bat` | Stops the RethinkDNS VPN service |

Equivalent ADB commands:

**Force-stop Instagram:**

```cmd
adb shell am force-stop com.instagram.android
```

**Enable RethinkDNS VPN:**

```cmd
adb shell am startservice -n com.celzero.rethink/.core.VpnService -a android.net.VpnService.START
```

**Disable RethinkDNS VPN:**

```cmd
adb shell am stopservice -n com.celzero.rethink/.core.VpnService
```

These commands assume the device has authorized ADB debugging and the corresponding service actions are supported by the installed RethinkDNS version.

---

## 5. Network Routing & Deployment Modes

CurfewDNS supports multiple network configurations.

The enforcement behavior depends on whether the Android device can reach the laptop-hosted DNS sinkhole.

### 5.1 Mode A — Windows Mobile Hotspot

The laptop acts as the Wi-Fi hotspot gateway.

**Default Windows Mobile Hotspot gateway:**

`192.168.137.1`

Android network configuration:

```text
IP assignment: DHCP
DNS assignment: Static
DNS 1: 192.168.137.1
```

Traffic flow:

```text
Android Device
      |
      | Wi-Fi
      v
Windows Mobile Hotspot
      |
      v
Laptop DNS Sinkhole
      |
      +---- LOCKED ----> Instagram domains blocked
      |
      +---- UNLOCKED --> Upstream DNS resolution
```

The Android device uses the laptop as its DNS resolver.

### 5.2 Mode B — Shared Wi-Fi Router

The Android device and laptop connect to the same Wi-Fi router.

The laptop must have a stable local IP address, preferably configured through a DHCP reservation on the router.

Android network configuration:

```text
IP assignment: DHCP
DNS assignment: Static
DNS 1: <YOUR_LAPTOP_LAN_IP>
```

**Important:** The laptop's DNS service must be reachable from the Android device over the local network. Windows Firewall must permit the required inbound traffic.

### 5.3 Mode C — Cellular / Off-LAN

When the Android device is disconnected from the laptop's network:

- The laptop-hosted DNS sinkhole is unavailable.
- Laptop-based schedule enforcement cannot be relied upon.
- RethinkDNS continues to provide locally configured UID-level firewalling and social DNS filtering.
- Application-level restrictions remain independent of the current Wi-Fi network.

This provides an additional enforcement layer even when the laptop is powered off or inaccessible.

However, the Android firewall does not automatically inherit the laptop's LOCKED/UNLOCKED schedule.

### 5.4 Network Mode Comparison

| Capability | Laptop Hotspot | Shared Router | Cellular / Off-LAN |
|---|---|---|---|
| Laptop DNS sinkhole | Yes | Yes | No |
| Centralized schedule | Yes | Yes | No |
| Android UID firewall | Yes | Yes | Yes |
| Social DNS blocklists | Yes | Yes | Yes |
| Laptop availability required | Yes | Yes | No |
| Enforcement independent of DNS | Through Android VPN | Through Android VPN | Through Android VPN |

---

## 6. Important Technical Discoveries & Fixes

### 6.1 PowerShell Encoding & Syntax

**Problem:**

Windows PowerShell 5.1 can misinterpret non-ASCII characters when scripts are saved using incompatible encodings.

Additionally, incorrect here-string syntax can cause parsing errors such as `AmpersandNotAllowed`.

**Resolution:**

1. Save all PowerShell `.ps1` files as UTF-8 with BOM.
2. Resolve paths dynamically using `$PSScriptRoot`.
3. Avoid hardcoded personal directory paths.
4. Use valid PowerShell array and here-string syntax.

**Array syntax:**

Correct:

```powershell
$days = @(1,2,3,4,5,6,7)
```

Or:

```powershell
$days = @(1..7)
```

Incorrect:

```powershell
$days = [1,2,3,4,5,6,7]
```

**Here-string syntax:**

Correct:

```powershell
$content = @"
Example content
Another line
"@
```

The opening and closing delimiters must follow PowerShell's here-string syntax, without additional characters or indentation on the delimiter lines.

**ADB installation:**

The Android setup script uses:

```powershell
winget install --id Google.PlatformTools -e --scope machine
```

After installation, the current PowerShell session can refresh its PATH:

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
```

### 6.2 Network & IP Configuration

To inspect Windows network adapter and IPv4 configuration:

```cmd
ipconfig
```

To filter adapter and IPv4 information:

```cmd
ipconfig | findstr /i "adapter IPv4"
```

For the Windows Mobile Hotspot configuration, the expected default gateway is:

```text
192.168.137.1
```

For shared-router deployment, use the laptop's actual LAN address:

```text
<YOUR_LAPTOP_LAN_IP>
```

A DHCP reservation is recommended to prevent the laptop's DNS address from changing unexpectedly.

### 6.3 Android Private DNS Limitation

The standalone Android Private DNS configuration scripts:

```text
scripts/android/android_private_dns_on.bat
scripts/android/android_private_dns_off.bat
```

are currently unused.

The original approach of configuring Android Private DNS directly for the locally generated self-signed DoT certificate was rejected because Android's Private DNS implementation requires an appropriately configured and trusted DNS-over-TLS endpoint.

CurfewDNS therefore relies on:
- The laptop's standard DNS service for local network enforcement.
- RethinkDNS for Android's on-device filtering.
- The laptop's DoT listener as an additional DNS transport option for compatible clients.

---

## 7. Repository Structure

The project follows a separation-of-concerns structure.

```text
CurfewDNS/
│
├── src/
│   └── dns_sinkhole.py
│
├── scripts/
│   │
│   ├── windows/
│   │   ├── setup.ps1
│   │   └── uninstall.ps1
│   │
│   └── android/
│       ├── android_setup.ps1
│       ├── android_force_stop_instagram.bat
│       ├── android_enable_vpn.bat
│       ├── android_disable_vpn.bat
│       ├── android_private_dns_on.bat
│       └── android_private_dns_off.bat
│
├── config/
│   ├── instagram_blocklist.txt
│   └── rethink_config.json
│
├── docs/
│   └── ARCHITECTURE.md
│
├── artifacts/
│   ├── rethink.apk
│   ├── dot_cert.pem
│   ├── dot_key.pem
│   └── runtime logs
│
├── requirements.txt
├── .gitignore
└── README.md
```

### 7.1 Component Responsibilities

| Component | Responsibility |
|---|---|
| `src/dns_sinkhole.py` | DNS interception, domain filtering, DoT, TTL management and HTTP dashboard |
| `scripts/windows/setup.ps1` | Windows deployment, firewall configuration and scheduled task creation |
| `scripts/windows/uninstall.ps1` | Windows deployment cleanup |
| `scripts/android/android_setup.ps1` | Android tooling installation and RethinkDNS setup |
| `scripts/android/*.bat` | ADB-based Android service management |
| `config/instagram_blocklist.txt` | Reference list of blocked Instagram/Meta domains |
| `config/rethink_config.json` | RethinkDNS configuration backup |
| `requirements.txt` | Python dependency specification |
| `README.md` | User-facing setup and troubleshooting documentation |
| `docs/ARCHITECTURE.md` | Technical architecture and design decisions |
| `artifacts/` | Generated certificates, downloaded binaries and runtime data |

---

## 8. Dependencies & Runtime Requirements

### 8.1 Python Dependencies

The project targets Python 3.10+.

`requirements.txt`:

```text
dnslib==0.9.24
cryptography>=45.0.7,<47
```

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

### 8.2 Windows Requirements

- Windows 11.
- Python 3.10 or later.
- Administrator privileges for firewall and scheduled task configuration.
- Windows Task Scheduler.
- Windows PowerShell 5.1 or compatible PowerShell.
- ADB Platform Tools for Android management.

### 8.3 Android Requirements

- Android device with VPN service support.
- RethinkDNS v0.5.8.
- ADB Platform Tools for automated setup and service management.
- Appropriate VPN permissions granted to RethinkDNS.

**Tested device:** OPPO F17 running ColorOS.

---

## 9. OPSEC, Security & Git Hygiene

CurfewDNS generates several sensitive or machine-specific artifacts during installation and execution.

These must remain outside version control.

### 9.1 Files That Must Never Be Committed

| Artifact | Reason |
|---|---|
| `*.pem` | TLS certificates and associated material |
| `*.key` | Private cryptographic keys |
| `*.apk` | Downloaded application binaries |
| `.venv/` | Local Python virtual environment |
| `*.log` | Runtime logs and potentially sensitive operational information |
| Machine-specific configuration | May expose local network details |

Generated files are isolated under:

```text
artifacts/
```

The directory is excluded through `.gitignore`.

### 9.2 Sanitization Requirements

Documentation, source examples and public repository content must never expose:

- Personal Windows directory paths.
- Personal home LAN IP addresses.
- ADB device serial numbers.
- TLS private key material.
- Machine-specific network configuration.
- Other personally identifying operational information.

Use generic placeholders instead:

```text
C:\Projects\CurfewDNS
<YOUR_LAPTOP_LAN_IP>
<YOUR_DEVICE_SERIAL>
```

### 9.3 Git Ignore Recommendations

The following patterns should be maintained in `.gitignore`:

```gitignore
# Python
.venv/
__pycache__/
*.py[cod]

# Generated artifacts
artifacts/
*.pem
*.key
*.apk

# Runtime logs
*.log

# Environment and local configuration
.env
.env.*
!.env.example

# IDE / OS files
.vscode/
.idea/
.DS_Store
Thumbs.db
```

If selected configuration backups are intentionally tracked, maintain explicit exceptions rather than broadly excluding the entire `config/` directory.

**Security note:** `.gitignore` prevents accidental future tracking; it does not remove files already committed to Git history. Previously committed secrets must be rotated and removed from repository history separately.

---

## 10. Operational Workflow

### 10.1 Initial Deployment

**Windows:**

1. Clone the CurfewDNS repository.
2. Install Python 3.10+.
3. Install dependencies using `requirements.txt`.
4. Generate the local DoT certificate and private key.
5. Run `scripts/windows/setup.ps1` with the required administrative privileges.
6. Verify that the `IG-DNS-Sinkhole` scheduled task has been created.
7. Verify that the DNS daemon and HTTP dashboard are accessible.

**Android:**

1. Install and configure RethinkDNS v0.5.8.
2. Configure the Instagram UID-level firewall rules.
3. Enable the Instagram / Social DNS blocklists.
4. Configure the appropriate network mode.
5. Grant the required VPN permissions.
6. Verify that application and browser restrictions work as expected.

### 10.2 Daily Enforcement Lifecycle

```text
                  DAILY CURFEW
                       |
                       v
               Read Local System Time
                       |
                       v
               Determine Curfew State
                       |
              +--------+--------+
              |                 |
              v                 v
            LOCKED           UNLOCKED
          06:00-23:30        23:30-06:00
              |                 |
              v                 v
        Blocklisted DNS     Upstream DNS
        domains blocked     forwarding
              |                 |
              v                 v
          TTL = 30s          TTL = 300s
              |                 |
              +--------+--------+
                       |
                       v
              Check Transition Window
                       |
                       v
               Within 10 Minutes?
                       |
                  +----+----+
                  |         |
                 Yes        No
                  |         |
                  v         v
               TTL=10s   Retain State TTL
                  |
                  v
             Repeat Monitoring
```

The transition TTL overrides the normal schedule TTL near both daily boundaries.

### 10.3 Verification Checklist

**Windows DNS Sinkhole:**

- [ ] Python daemon starts successfully.
- [ ] UDP port 53 is reachable.
- [ ] TCP port 53 is reachable.
- [ ] TCP port 853 is available for compatible DoT clients.
- [ ] HTTP dashboard responds on port 8080.
- [ ] Instagram/Meta domains are blocked during LOCKED hours.
- [ ] Normal upstream DNS resolution works during UNLOCKED hours.
- [ ] Dynamic TTL changes are applied correctly.
- [ ] Midnight crossover is handled correctly.
- [ ] Windows scheduled task is registered and operational.

**Android Enforcement:**

- [ ] RethinkDNS VPN service starts successfully.
- [ ] Instagram UID-level firewall rules are active.
- [ ] Instagram native application cannot bypass configured firewall restrictions.
- [ ] Social DNS blocklists are enabled.
- [ ] Instagram mobile web access is restricted.
- [ ] Enforcement continues when switching between Wi-Fi networks and cellular connectivity.

**Repository Hygiene:**

- [ ] No private keys or certificates are tracked.
- [ ] No downloaded APKs are tracked.
- [ ] No runtime logs are tracked.
- [ ] No personal paths or LAN IP addresses appear in public documentation.
- [ ] PowerShell scripts are saved as UTF-8 with BOM.

---

## 11. Known Limitations & Future Improvements

CurfewDNS is designed as a practical defense-in-depth enforcement system, but several limitations remain important.

### 11.1 Current Limitations

1. **Laptop dependency:** Scheduled DNS enforcement requires the laptop-hosted sinkhole to be running and reachable.

2. **DNS bypasses:** DNS filtering alone cannot reliably prevent DoH, alternative resolvers, hardcoded IP connections or previously established sessions.

3. **Android schedule synchronization:** RethinkDNS v0.5.8 does not independently synchronize its UID-level firewall state with the laptop's curfew schedule.

4. **Self-signed DoT certificate:** Compatible clients must explicitly trust the generated certificate.

5. **TTL limitations:** Short DNS TTLs improve transition responsiveness but cannot forcibly terminate established connections or flush every application-level cache.

6. **Network configuration:** Shared-router deployment requires a stable laptop LAN address and correctly configured firewall access.

### 11.2 Potential Future Improvements

- Introduce a centralized state synchronization mechanism between the Windows daemon and Android enforcement layer.
- Add authenticated local status and control endpoints.
- Improve monitoring of DNS bypass attempts.
- Add automated health checks for the sinkhole daemon and scheduled task.
- Provide a more detailed dashboard with historical query statistics.
- Expand domain coverage through maintained blocklist sources.
- Add automated testing for midnight crossover and schedule-boundary conditions.
- Introduce stronger protection against alternative DNS and encrypted DNS bypasses.
- Explore automated Android firewall state transitions based on the laptop's curfew state.

---

## 12. Design Summary

CurfewDNS uses two complementary enforcement mechanisms to address the weaknesses of conventional DNS-only blocking.

**Windows 11 DNS Sinkhole:**
- Centralized domain-level enforcement.
- Automated daily curfew scheduling.
- UDP/TCP DNS and DNS-over-TLS support.
- Dynamic TTL-based cache expiration.
- HTTP monitoring dashboard.
- Windows Task Scheduler integration.

**Android RethinkDNS:**
- Rootless application-level firewalling.
- UID-based network restrictions.
- Protection against hardcoded IP and transport-level bypasses, subject to configured rules.
- Built-in social DNS blocklists.
- Enforcement independent of the laptop's network availability.

The central architectural decision is to separate **schedule-aware network filtering** from **device-level application enforcement**.

This provides a practical foundation for restricting distracting applications across multiple network environments while keeping the implementation modular, maintainable and extensible.

**Project:** CurfewDNS  
**Primary target:** Instagram (Android application + mobile web)  
**Host platform:** Windows 11  
**Client platform:** Android  
**DNS implementation:** Python 3.10+ / dnslib  
**Android enforcement:** RethinkDNS v0.5.8  
**Daily curfew:** 06:00–23:30 LOCKED / 23:30–06:00 UNLOCKED
