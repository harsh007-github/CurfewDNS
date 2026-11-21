# CurfewDNS — Hybrid Time-Window Blocker (Laptop DNS + Android On-Device)

**CurfewDNS** is a hybrid, cross-device network enforcement system designed to block distracting applications and domains during a predefined daily curfew window.

Initially configured for **Instagram**, CurfewDNS combines a laptop-hosted DNS sinkhole with Android on-device enforcement to restrict access to Instagram across both the native Android application and mobile browsers.

**Repository:** [github.com/harsh007-github/CurfewDNS](https://github.com/harsh007-github/CurfewDNS)

## Daily Enforcement Schedule

CurfewDNS follows a fixed daily schedule using local system time.

| Window | Local Time | State |
|:---|:---|:---|
| **LOCKED** | 06:00 – 23:30 | BLOCKED |
| **UNLOCKED** | 23:30 – 06:00 | ALLOWED |

The enforcement state automatically changes according to the configured schedule.

---

## Hybrid Two-Layer Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│          Layer 1 — Laptop-Hosted DNS Sinkhole                       │
│                  src/dns_sinkhole.py                                 │
│                                                                     │
│  • Hosted on a Windows 11 laptop (Python 3.10+)                     │
│  • Standard DNS: UDP/TCP :53                                        │
│  • DNS-over-TLS: TCP :853                                            │
│  • HTTP Live Status Dashboard: TCP :8080                             │
│  • Returns NXDOMAIN + 0.0.0.0 / :: for blocked domains              │
│  • Dynamic three-tier TTL switching:                                │
│      - 10s  → Within 10 minutes of schedule transitions             │
│      - 30s  → During LOCKED hours                                   │
│      - 300s → During UNLOCKED hours                                 │
│  • Serves Android clients through the laptop hotspot or home LAN    │
│                                                                     │
│  Coverage: Same network as the laptop                               │
└─────────────────────────────────────────────────────────────────────┘
                                ▲
                                │
                     Phone on the same LAN
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│          Layer 2 — Android On-Device Enforcement                     │
│                   RethinkDNS v0.5.8                                  │
│                                                                     │
│  • Uses Android's VPN API (root access not required)                │
│  • UID-level firewall rules restrict the target application         │
│  • Blocks hardcoded IP and QUIC-based bypass attempts               │
│  • Built-in Instagram / Social DNS blocklists restrict web access   │
│  • Operates independently of the laptop-hosted DNS server           │
│                                                                     │
│  Coverage: Cellular data, external Wi-Fi, and laptop-off scenarios  │
└─────────────────────────────────────────────────────────────────────┘
```

### Layer 1 — Windows Laptop DNS Sinkhole

**Core implementation:** `src/dns_sinkhole.py`

The laptop runs a Python-based DNS sinkhole that intercepts DNS queries and enforces the configured domain-blocking schedule.

**Services and ports**

| Service | Protocol | Port | Purpose |
|:---|:---|:---|:---|
| Standard DNS | UDP/TCP | 53 | DNS query interception and filtering |
| DNS-over-TLS | TCP | 853 | Encrypted DNS query handling |
| Status Dashboard | HTTP | 8080 | Live enforcement status |

**Domain blocking**

During LOCKED hours, queries for configured target domains receive blocked DNS responses:

- `NXDOMAIN` response.
- IPv4 sinkhole address: `0.0.0.0`.
- IPv6 sinkhole address: `::`.

The target domains are maintained in:

`config/instagram_blocklist.txt`

This makes the blocklist configurable without hardcoding every target domain into the DNS server.

**Dynamic TTL switching**

CurfewDNS uses a three-tier Time-To-Live (TTL) strategy to reduce stale DNS cache issues around schedule transitions.

| Enforcement Period | DNS TTL |
|:---|---:|
| Within 10 minutes of 06:00 or 23:30 | 10 seconds |
| LOCKED hours | 30 seconds |
| UNLOCKED hours | 300 seconds |

The shorter TTL near transitions helps clients refresh DNS records sooner when the enforcement state changes.

**Windows automation**

Windows-specific setup and automation are handled through:

- `scripts/windows/setup.ps1`
- Windows Firewall configuration.
- Python virtual environment setup.
- Local TLS certificate generation.
- Windows Task Scheduler integration.

The scheduled task is named:

`IG-DNS-Sinkhole`

The PowerShell setup script resolves project paths dynamically using `$PSScriptRoot`, avoiding hardcoded machine-specific paths.

**Network connectivity**

Android devices can use the laptop-hosted DNS service through either:

1. Windows Mobile Hotspot — default gateway/DNS host: `192.168.137.1`.
2. Home Wi-Fi Router — configure the laptop's LAN address as the DNS server:

   `<YOUR_LAPTOP_LAN_IP>`

Layer 1 enforcement requires the Android device to be connected to a network that can reach the laptop.

### Layer 2 — Android On-Device Enforcement

**Implementation:** `scripts/android/`  
**Enforcement engine:** RethinkDNS v0.5.8

The Android layer provides independent enforcement when the laptop is unavailable or the device is connected to a different network.

It uses Android's built-in VPN API through RethinkDNS, without requiring root access.

**Application-level firewall**

- Applies firewall restrictions to the target application's UID.
- Prevents the target application from accessing network resources.
- Helps prevent bypasses involving hardcoded IP addresses.
- Restricts QUIC / HTTP/3-based connectivity that could otherwise bypass conventional DNS filtering.

**DNS-level filtering**

RethinkDNS also uses its built-in:

`Instagram / Social`

DNS blocklist to restrict access to Instagram through mobile browsers and other applications making matching DNS requests.

**Independent operation**

Unlike Layer 1, Android enforcement does not depend on the laptop or home network.

It remains available when:

- The phone switches to cellular data.
- The phone connects to external Wi-Fi networks.
- The laptop is powered off.
- The phone is away from the home network.

---

## Repository Structure

```text
CurfewDNS/
│
├── src/
│   └── dns_sinkhole.py
│
├── scripts/
│   ├── windows/
│   │   └── setup.ps1
│   │
│   └── android/
│       └── android_setup.ps1
│
├── config/
│   └── instagram_blocklist.txt
│
├── docs/
│   └── ARCHITECTURE.md
│
├── README.md
└── .gitignore
```

## Prerequisites

### Windows Laptop

- Windows 11.
- Python 3.10 or later.
- Administrator privileges for DNS port binding and firewall configuration.
- PowerShell.
- A network adapter supporting Windows Mobile Hotspot, if hotspot connectivity is required.

### Android Device

- Android device with VPN API support.
- RethinkDNS v0.5.8.
- USB debugging and ADB for the optional setup helper.
- Permission to establish an Android VPN connection.

---

## Installation and Setup

### 1. Clone the Repository

```powershell
git clone https://github.com/harsh007-github/CurfewDNS.git

cd CurfewDNS
```

### 2. Configure the Windows DNS Sinkhole

Run PowerShell as Administrator:

```powershell
Set-ExecutionPolicy -Scope Process Bypass

.\scripts\windows\setup.ps1
```

The setup script handles the Windows-side environment, including:

- Python virtual environment preparation.
- Required dependencies.
- Local TLS certificate generation.
- Windows Firewall configuration.
- Windows Task Scheduler registration.

### 3. Configure the Android Device

Connect your Android device using ADB and run:

```powershell
.\scripts\android\android_setup.ps1
```

Install and configure RethinkDNS v0.5.8 on the device.

Enable the application-level firewall and the Instagram / Social DNS blocklist according to the Android configuration.

### 4. Configure Network Connectivity

For Layer 1 enforcement:

- Connect the phone to the laptop's Windows Mobile Hotspot or the same home LAN.
- Configure the laptop as the DNS server.
- Ensure the relevant DNS traffic can reach the laptop.

For Layer 2 enforcement:

- Keep RethinkDNS active.
- Verify that the target application's firewall rules are enabled.
- Verify that the relevant DNS blocklists are enabled.

---

## Live Status Dashboard

The laptop-hosted DNS sinkhole exposes an HTTP status dashboard on port `8080`.

Open the following address from a device that can reach the laptop:

```text
http://<YOUR_LAPTOP_LAN_IP>:8080
```

The dashboard provides a live view of the DNS sinkhole's enforcement status.

---

## Design Decisions

| Design Choice | Reason |
|:---|:---|
| Hybrid enforcement | Combines network-level and device-level restrictions |
| DNS sinkhole | Blocks configured domains without modifying individual applications |
| Android VPN API | Enables rootless, device-level network enforcement |
| UID-level firewall | Restricts application traffic beyond DNS filtering |
| QUIC restrictions | Helps prevent DNS-based enforcement bypasses |
| Dynamic TTL | Reduces stale DNS cache issues during schedule transitions |
| Windows Task Scheduler | Automates the laptop-hosted service |
| Configurable blocklist | Allows additional domains to be maintained separately from application code |

## Important Limitations

- **Layer 1 dependency:** Laptop-hosted enforcement is only available when the Android device can reach the laptop.
- **DNS bypasses:** Applications using alternative DNS resolvers, encrypted DNS, cached IP addresses, or direct IP connections may bypass conventional DNS filtering.
- **Android VPN dependency:** RethinkDNS must remain active, with the appropriate firewall and DNS configurations enabled.
- **VPN exclusivity:** Android generally permits only one active VPN service at a time, which can affect compatibility with other VPN applications.
- **TTL limitations:** DNS TTL values influence compliant DNS caches but cannot force every application or operating system to discard independently cached records.
- **Application coverage:** Blocking the Instagram UID and filtering social DNS domains are separate mechanisms. Their combined effectiveness depends on the configured firewall rules and blocklists.
- **Certificate trust:** Locally generated self-signed TLS certificates require appropriate client-side trust/configuration for DNS-over-TLS connections.

CurfewDNS is designed as a layered enforcement system rather than relying exclusively on DNS filtering. Its two-layer architecture aims to provide consistent restrictions across the laptop's network and independent Android network environments.

---

## Future Scope

Potential improvements include:

- Support for multiple configurable applications and domain groups.
- A centralized configuration file for schedules and enforcement targets.
- Improved enforcement-state synchronization between Windows and Android.
- Detailed DNS query and firewall event logging.
- Additional network-level bypass detection.
- A more comprehensive monitoring dashboard.
- Configuration backup and restoration.

---

## License

See the repository's license file for licensing information.