# CurfewDNS — Hybrid Time-Window Blocker (Laptop DNS + Android On-Device)

**CurfewDNS** (`https://github.com/harsh007-github/CurfewDNS`) enforces a strict daily schedule for distracting apps and domains (initially configured for **Instagram** across the Android app and mobile web):

| Window | Local Time | State |
|--------|------------|-------|
| **LOCKED**   | 06:00 – 23:30 | **BLOCKED** |
| **UNLOCKED** | 23:30 – 06:00 | **ALLOWED** |

---

## Hybrid Two-Layer Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1 — Laptop-Hosted DNS Sinkhole (src/dns_sinkhole.py)     │
│ • Runs on Windows 11 laptop (Python 3.10+)                     │
│ • UDP/TCP DNS on :53  • DoT on :853  • HTTP Dashboard on :8080 │
│ • Blocks target domains via NXDOMAIN + 0.0.0.0/::              │
│ • Dynamic TTL switching (10s near transitions / 30s locked,     │
│   300s unlocked) for clean cache flushes on state changes       │
│ • Works when phone is on laptop's Wi-Fi Hotspot or home router │
└─────────────────────────────────────────────────────────────────┘
                              ↑
                    Phone on same LAN
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ Layer 2 — On-Device Enforcement (RethinkDNS v0.5.8 on Android) │
│ • Android VPN API (no root)                                    │
│ • Firewall: blocks target app UID (stops hardcoded IPs/QUIC)   │
│ • DNS: built-in "Instagram / Social" blocklist (stops web)     │
│ • Works on cellular, any Wi-Fi, laptop off                     │
└─────────────────────────────────────────────────────────────────┘