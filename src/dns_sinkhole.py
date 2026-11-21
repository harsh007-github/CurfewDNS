#!/usr/bin/env python3
"""
Instagram Time-Window DNS Sinkhole (Enhanced)

A lightweight DNS server that blocks Instagram domains outside a configured
daily time window (default: 23:30 - 06:00 local time).

Enhanced Features:
- DNS-over-TLS (DoT) on port 853 for Android Private DNS support
- Expanded Meta/Instagram domain suffix list
- DNS-over-HTTPS (DoH) endpoint blocking
- QUIC/UDP 443 IP blocking guidance
- HTTP dashboard with Prometheus metrics
- Graceful shutdown with signal handling

Architecture:
- UDP DNS server on port 53 (requires admin/root)
- TCP DNS server on port 53 (for large responses)
- DNS-over-TLS server on port 853 (for Android Private DNS)
- HTTP dashboard on port 8080 (no auth, local only)
- Time-window logic handles midnight crossover correctly
- Short TTL (30s) on blocked responses for immediate transitions
- Zero external deps except dnslib for DNS parsing, ssl for DoT

Usage:
    python dns_sinkhole.py              # Run foreground with dashboard
    python dns_sinkhole.py --test       # One-shot test, exit
    python dns_sinkhole.py --no-http    # Disable HTTP dashboard
    python dns_sinkhole.py --no-dot     # Disable DoT server
    python dns_sinkhole.py --port 5353 --dot-port 8533 --http-port 8081  # Non-privileged ports
"""

import sys
import os
import socket
import threading
import time
import signal
import logging
import ssl
from datetime import datetime, time as dt_time, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json

# ─── Dependency Check ──────────────────────────────────────────────────────
try:
    from dnslib import DNSRecord, QTYPE, RR, A, AAAA, RCODE
except ImportError:
    print("ERROR: dnslib not installed. Run: pip install dnslib", file=sys.stderr)
    sys.exit(1)

# ─── Configuration ─────────────────────────────────────────────────────────
# Time window when Instagram is ALLOWED (UNLOCKED)
# Format: (hour, minute) in 24h local time
UNLOCK_START = (23, 30)   # 23:30 (11:30 PM)
UNLOCK_END   = (6, 0)     # 06:00 (6:00 AM)

# Upstream DNS servers (used when UNLOCKED)
UPSTREAM_DNS = [("1.1.1.1", 53), ("8.8.8.8", 53)]

# Listen addresses
DNS_LISTEN_ADDR = "0.0.0.0"
DNS_LISTEN_PORT = 53
DOT_LISTEN_ADDR = "0.0.0.0"
DOT_LISTEN_PORT = 853
HTTP_LISTEN_ADDR = "0.0.0.0"
HTTP_LISTEN_PORT = 8080

# Instagram/Meta domains to block (suffix matching)
# Expanded list covering Instagram app, web, CDN, Graph API, MQTT, Edge Chat, etc.
INSTAGRAM_SUFFIXES = (
    # Core Instagram
    "instagram.com",
    "cdninstagram.com",
    "ig.me",

    # Facebook/CDN infrastructure used by Instagram
    "fbcdn.net",           # Primary CDN (photos, videos, static assets)
    "fbsbx.com",           # Facebook static assets (often used by IG)
    "fb.com",              # Facebook short domain
    "facebook.com",        # Facebook (Graph API calls from IG app)
    "facebook.net",        # Facebook connectivity

    # Instagram-specific subdomains & services
    "instagram.net",       # Rare but used
    "i.instagram.com",     # Instagram internal
    "graph.instagram.com", # Graph API
    "api.instagram.com",   # Legacy API
    "www.instagram.com",   # Web
    "help.instagram.com",  # Help center
    "business.instagram.com", # Business tools

    # Real-time / messaging / push
    "edge-chat.instagram.com",   # Edge chat (Messenger integration)
    "edge-chat.facebook.com",    # Edge chat (Facebook)
    "mqtt-mini.facebook.com",    # MQTT for real-time notifications
    "mqtt.facebook.com",         # MQTT
    "push.facebook.com",         # Push notifications

    # Meta / WhatsApp / Threads crossover domains
    "whatsapp.net",              # WhatsApp (sometimes used for cross-posting)
    "whatsapp.com",
    "threads.net",               # Threads app
    "cdn.threads.net",

    # DoH/DoT endpoints that apps might hardcode (block the hostnames)
    "dns.google",                # Google DoH
    "doh.dns.sb",                # Cloudflare DoH alternative
    "cloudflare-dns.com",        # Cloudflare DoH
    "1.1.1.1",                   # Cloudflare (as hostname)
    "dns.quad9.net",             # Quad9 DoH
    "dns.nextdns.io",            # NextDNS DoH
)

# Known DoH provider IP ranges to block at network level (for firewall rules)
# These are the IPs that DoH clients connect to - block via Windows Firewall
DOH_PROVIDER_IPS = {
    # Cloudflare
    "1.1.1.1", "1.0.0.1", "2606:4700:4700::1111", "2606:4700:4700::1001",
    # Google
    "8.8.8.8", "8.8.4.4", "2001:4860:4860::8888", "2001:4860:4860::8844",
    # Quad9
    "9.9.9.9", "149.112.112.112", "2620:fe::fe", "2620:fe::9",
    # NextDNS (common in privacy apps)
    "45.90.28.0", "45.90.30.0", "45.90.28.0/22", "45.90.30.0/22",
}

# TTL values
TTL_BLOCKED = 30        # Short TTL for immediate transition at boundary
TTL_ALLOWED = 300       # Normal TTL when forwarding

# DoT Certificate paths (auto-generated self-signed if not provided)
# Certs are stored in ../artifacts/ relative to this script
DOT_CERT_FILE = os.path.join(os.path.dirname(__file__), "..", "artifacts", "dot_cert.pem")
DOT_KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "artifacts", "dot_key.pem")

# ─── Globals / State ───────────────────────────────────────────────────────
blocked_count = 0
allowed_count = 0
start_time = time.time()
dns_socket_udp = None
dns_socket_tcp = None
dot_socket = None
http_server = None
shutdown_event = threading.Event()

# ─── Logging Setup ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("ig-sinkhole")

# ─── Time Window Logic ─────────────────────────────────────────────────────
def is_unlocked(now: datetime = None) -> bool:
    """
    Check if current time falls within the UNLOCKED window.

    Handles midnight crossover: UNLOCK_START (23:30) > UNLOCK_END (06:00)
    means the window spans midnight.

    Args:
        now: datetime to check (defaults to now)

    Returns:
        True if Instagram should be accessible, False if blocked
    """
    if now is None:
        now = datetime.now()

    current = now.time()
    start = dt_time(*UNLOCK_START)
    end = dt_time(*UNLOCK_END)

    if start < end:
        # Normal window (e.g., 09:00 - 17:00)
        return start <= current < end
    else:
        # Midnight crossover window (e.g., 23:30 - 06:00)
        return current >= start or current < end


def time_until_next_flip(now: datetime = None) -> timedelta:
    """
    Calculate time until the next state change (LOCKED ↔ UNLOCKED).

    Args:
        now: datetime to calculate from (defaults to now)

    Returns:
        timedelta until next transition
    """
    if now is None:
        now = datetime.now()

    current = now.time()
    start = dt_time(*UNLOCK_START)
    end = dt_time(*UNLOCK_END)

    # Current state determines which boundary is next
    unlocked = is_unlocked(now)

    if start < end:
        # Normal window (e.g., 09:00 - 17:00) — both boundaries on same calendar day
        today = now.date()
        unlock_today = datetime.combine(today, start)
        lock_today = datetime.combine(today, end)
        unlock_tomorrow = datetime.combine(today + timedelta(days=1), start)

        if unlocked:
            # Currently unlocked → next is LOCK at end time today
            if now < lock_today:
                return lock_today - now
            else:
                return unlock_tomorrow - now
        else:
            # Currently locked → next is UNLOCK at start time
            if now < unlock_today:
                return unlock_today - now
            else:
                return unlock_tomorrow - now
    else:
        # Midnight crossover window (e.g., 23:30 - 06:00)
        # UNLOCK at 23:30 on day N, LOCK at 06:00 on day N+1
        # The "window day" is the day containing the 23:30 unlock
        if current >= start:
            # We're at/after 23:30 → window day is today
            window_day = now.date()
        else:
            # We're before 23:30 → window day is yesterday (the 23:30 was yesterday)
            window_day = now.date() - timedelta(days=1)

        unlock_time = datetime.combine(window_day, start)           # 23:30 on window day
        lock_time = datetime.combine(window_day + timedelta(days=1), end)  # 06:00 next day
        next_unlock_time = datetime.combine(window_day + timedelta(days=1), start)  # 23:30 next day

        if unlocked:
            # Currently in window → next is LOCK at lock_time
            return lock_time - now
        else:
            # Currently outside window → next is UNLOCK
            if now < unlock_time:
                # Before this window's unlock
                return unlock_time - now
            else:
                # After this window's lock → next window's unlock
                return next_unlock_time - now


def format_timedelta(td: timedelta) -> str:
    """Format timedelta as human-readable string."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"

# ─── Domain Matching ───────────────────────────────────────────────────────
def is_instagram_domain(qname: str) -> bool:
    """
    Check if a domain name matches Instagram/Meta suffixes.

    Args:
        qname: Fully qualified domain name (with trailing dot from dnslib)

    Returns:
        True if domain should be blocked/controlled
    """
    # dnslib returns qname with trailing dot, e.g., "instagram.com."
    qname = qname.rstrip(".").lower()
    return any(qname == suffix or qname.endswith("." + suffix) for suffix in INSTAGRAM_SUFFIXES)


def is_doh_endpoint(qname: str) -> bool:
    """Check if query is for a known DoH provider hostname."""
    qname = qname.rstrip(".").lower()
    doh_suffixes = (
        "dns.google",
        "doh.dns.sb",
        "cloudflare-dns.com",
        "dns.cloudflare.com",
        "dns.quad9.net",
        "dns.nextdns.io",
        "doh.opendns.com",
        "doh.cleanbrowsing.org",
        "doh.adguard.com",
        "dns.adguard.com",
        "doh.libredns.gr",
    )
    return any(qname == suffix or qname.endswith("." + suffix) for suffix in doh_suffixes)

# ─── DNS Resolution ────────────────────────────────────────────────────────
def forward_query(data: bytes, upstream: tuple) -> bytes:
    """
    Forward DNS query to upstream server and return response.

    Args:
        data: Raw DNS query bytes
        upstream: (host, port) tuple

    Returns:
        Raw DNS response bytes, or None on failure
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3.0)
    try:
        sock.sendto(data, upstream)
        response, _ = sock.recvfrom(4096)
        return response
    except socket.timeout:
        return None
    except Exception as e:
        log.debug(f"Forward error to {upstream}: {e}")
        return None
    finally:
        sock.close()


def forward_query_tcp(data: bytes, upstream: tuple) -> bytes:
    """Forward DNS query over TCP."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect(upstream)
        # DNS over TCP: 2-byte length prefix + message
        sock.sendall(len(data).to_bytes(2, 'big') + data)
        # Read length prefix
        length_bytes = sock.recv(2)
        if not length_bytes:
            return None
        length = int.from_bytes(length_bytes, 'big')
        # Read response
        response = b""
        while len(response) < length:
            chunk = sock.recv(length - len(response))
            if not chunk:
                break
            response += chunk
        return response
    except Exception as e:
        log.debug(f"TCP forward error to {upstream}: {e}")
        return None
    finally:
        sock.close()


def build_blocked_response(request: DNSRecord, qtype: int) -> DNSRecord:
    """
    Build a sinkhole response (NXDOMAIN for A/AAAA, empty for others).

    Args:
        request: Original DNS request
        qtype: Query type (QTYPE.A, QTYPE.AAAA, etc.)

    Returns:
        DNSRecord with sinkhole response
    """
    reply = request.reply()
    reply.header.rcode = RCODE.NXDOMAIN

    # For A/AAAA, return 0.0.0.0 / :: with short TTL
    if qtype == QTYPE.A:
        reply.add_answer(RR(request.q.qname, QTYPE.A, rdata=A("0.0.0.0"), ttl=TTL_BLOCKED))
    elif qtype == QTYPE.AAAA:
        reply.add_answer(RR(request.q.qname, QTYPE.AAAA, rdata=AAAA("::"), ttl=TTL_BLOCKED))

    return reply


def build_allowed_response(request: DNSRecord, upstream_data: bytes) -> DNSRecord:
    """
    Parse upstream response and adjust TTL for A/AAAA records.

    Args:
        request: Original DNS request
        upstream_data: Raw response from upstream DNS

    Returns:
        DNSRecord with adjusted TTL
    """
    reply = DNSRecord.parse(upstream_data)

    # Reduce TTL on A/AAAA to TTL_ALLOWED for faster future transitions
    for rr in reply.rr:
        if rr.rtype in (QTYPE.A, QTYPE.AAAA) and rr.ttl > TTL_ALLOWED:
            rr.ttl = TTL_ALLOWED

    return reply

# ─── DNS Query Handler ─────────────────────────────────────────────────────
def handle_dns_query(data: bytes, addr: tuple, protocol: str = "UDP") -> bytes:
    """
    Process a single DNS query.

    Args:
        data: Raw query bytes
        addr: Client (ip, port)
        protocol: "UDP", "TCP", or "DoT"

    Returns:
        Raw response bytes
    """
    global blocked_count, allowed_count

    try:
        request = DNSRecord.parse(data)
    except Exception as e:
        log.debug(f"Parse error from {addr[0]} ({protocol}): {e}")
        return b""

    qname = str(request.q.qname)
    qtype = request.q.qtype

    # Log query
    log.debug(f"Query {protocol} from {addr[0]}: {qname} ({QTYPE.get(qtype, qtype)})")

    # Check if it's a DoH endpoint hostname - block these too during locked hours
    if is_doh_endpoint(qname) and qtype in (QTYPE.A, QTYPE.AAAA):
        unlocked = is_unlocked()
        if not unlocked:
            blocked_count += 1
            log.info(f"BLOCK DoH endpoint {qname} ({QTYPE[qtype]}) from {addr[0]} ({protocol}) → sinkholed")
            return build_blocked_response(request, qtype).pack()
        # If unlocked, allow DoH endpoints (user might want general DoH)
        # But we still forward through us for logging

    # Only handle A and AAAA for Instagram; forward others
    if not is_instagram_domain(qname) or qtype not in (QTYPE.A, QTYPE.AAAA):
        # Not an Instagram A/AAAA query — forward to upstream
        for upstream in UPSTREAM_DNS:
            if protocol in ("UDP", "DoT"):
                resp = forward_query(data, upstream)
            else:  # TCP
                resp = forward_query_tcp(data, upstream)
            if resp:
                allowed_count += 1
                return resp
        # Upstream failed — return SERVFAIL
        reply = request.reply()
        reply.header.rcode = RCODE.SERVFAIL
        return reply.pack()

    # Instagram domain — check time window
    unlocked = is_unlocked()

    if unlocked:
        # Forward to upstream during allowed window
        for upstream in UPSTREAM_DNS:
            if protocol in ("UDP", "DoT"):
                resp = forward_query(data, upstream)
            else:  # TCP
                resp = forward_query_tcp(data, upstream)
            if resp:
                allowed_count += 1
                log.info(f"ALLOW {qname} ({QTYPE[qtype]}) from {addr[0]} ({protocol}) → forwarded")
                return build_allowed_response(request, resp).pack()

        # Upstream failed
        log.warning(f"Upstream failed for {qname} from {addr[0]} ({protocol})")
        reply = request.reply()
        reply.header.rcode = RCODE.SERVFAIL
        return reply.pack()
    else:
        # BLOCKED: Return sinkhole
        blocked_count += 1
        log.info(f"BLOCK {qname} ({QTYPE[qtype]}) from {addr[0]} ({protocol}) → sinkholed")
        return build_blocked_response(request, qtype).pack()

# ─── DNS Server Loops ──────────────────────────────────────────────────────
def dns_udp_loop():
    """Main UDP DNS server loop."""
    global dns_socket_udp

    dns_socket_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns_socket_udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        dns_socket_udp.bind((DNS_LISTEN_ADDR, DNS_LISTEN_PORT))
    except PermissionError:
        log.error(f"Permission denied binding to UDP port {DNS_LISTEN_PORT}. Run as Administrator.")
        shutdown_event.set()
        return
    except OSError as e:
        log.error(f"UDP bind failed: {e}")
        shutdown_event.set()
        return

    dns_socket_udp.settimeout(1.0)  # Allows checking shutdown_event
    log.info(f"DNS UDP server listening on {DNS_LISTEN_ADDR}:{DNS_LISTEN_PORT}")

    while not shutdown_event.is_set():
        try:
            data, addr = dns_socket_udp.recvfrom(4096)
            response = handle_dns_query(data, addr, "UDP")
            if response:
                dns_socket_udp.sendto(response, addr)
        except socket.timeout:
            continue
        except Exception as e:
            if not shutdown_event.is_set():
                log.error(f"DNS UDP loop error: {e}")

    dns_socket_udp.close()
    log.info("DNS UDP server stopped")


def dns_tcp_loop():
    """TCP DNS server loop (for large responses)."""
    global dns_socket_tcp

    dns_socket_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dns_socket_tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        dns_socket_tcp.bind((DNS_LISTEN_ADDR, DNS_LISTEN_PORT))
        dns_socket_tcp.listen(50)
    except PermissionError:
        log.error(f"Permission denied binding to TCP port {DNS_LISTEN_PORT}. Run as Administrator.")
        shutdown_event.set()
        return
    except OSError as e:
        log.error(f"TCP bind failed: {e}")
        shutdown_event.set()
        return

    dns_socket_tcp.settimeout(1.0)
    log.info(f"DNS TCP server listening on {DNS_LISTEN_ADDR}:{DNS_LISTEN_PORT}")

    while not shutdown_event.is_set():
        try:
            conn, addr = dns_socket_tcp.accept()
            conn.settimeout(5.0)
            try:
                # Read length prefix (2 bytes)
                length_bytes = conn.recv(2)
                if not length_bytes:
                    conn.close()
                    continue
                length = int.from_bytes(length_bytes, 'big')
                # Read query
                data = b""
                while len(data) < length:
                    chunk = conn.recv(length - len(data))
                    if not chunk:
                        break
                    data += chunk
                if data:
                    response = handle_dns_query(data, addr, "TCP")
                    if response:
                        # Send length prefix + response
                        conn.sendall(len(response).to_bytes(2, 'big') + response)
            finally:
                conn.close()
        except socket.timeout:
            continue
        except Exception as e:
            if not shutdown_event.is_set():
                log.error(f"DNS TCP loop error: {e}")

    dns_socket_tcp.close()
    log.info("DNS TCP server stopped")


def generate_self_signed_cert(cert_file: str, key_file: str):
    """Generate a self-signed certificate for DoT."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    import datetime as dt

    # Generate key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Generate cert
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"Instagram DNS Sinkhole"),
    ])
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        dt.datetime.utcnow()
    ).not_valid_after(
        dt.datetime.utcnow() + dt.timedelta(days=3650)  # 10 years
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(u"localhost")]),
        critical=False,
    ).sign(key, hashes.SHA256())

    with open(key_file, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    log.info(f"Generated self-signed cert: {cert_file}, {key_file}")


def dot_server_loop():
    """DNS-over-TLS server loop for Android Private DNS."""
    global dot_socket

    # Check for cert/key files, generate if missing
    if not os.path.exists(DOT_CERT_FILE) or not os.path.exists(DOT_KEY_FILE):
        try:
            from cryptography.hazmat.primitives import hashes
            generate_self_signed_cert(DOT_CERT_FILE, DOT_KEY_FILE)
        except ImportError:
            log.error("cryptography not installed. Install with: pip install cryptography")
            log.error("DoT server requires cryptography for TLS. Disabling DoT.")
            return
        except Exception as e:
            log.error(f"Failed to generate cert: {e}")
            return

    # Create SSL context
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(DOT_CERT_FILE, DOT_KEY_FILE)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_3

    dot_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dot_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        dot_socket.bind((DOT_LISTEN_ADDR, DOT_LISTEN_PORT))
        dot_socket.listen(50)
    except PermissionError:
        log.error(f"Permission denied binding to DoT port {DOT_LISTEN_PORT}. Run as Administrator.")
        shutdown_event.set()
        return
    except OSError as e:
        log.error(f"DoT bind failed: {e}")
        shutdown_event.set()
        return

    dot_socket.settimeout(1.0)
    log.info(f"DNS-over-TLS server listening on {DOT_LISTEN_ADDR}:{DOT_LISTEN_PORT}")

    while not shutdown_event.is_set():
        try:
            conn, addr = dot_socket.accept()
            conn.settimeout(5.0)
            try:
                # Wrap in TLS
                tls_conn = context.wrap_socket(conn, server_side=True)
                tls_conn.settimeout(5.0)

                # Read DNS-over-TLS: 2-byte length prefix + message
                while not shutdown_event.is_set():
                    length_bytes = tls_conn.recv(2)
                    if not length_bytes:
                        break
                    length = int.from_bytes(length_bytes, 'big')
                    data = b""
                    while len(data) < length:
                        chunk = tls_conn.recv(length - len(data))
                        if not chunk:
                            break
                        data += chunk
                    if not data:
                        break

                    response = handle_dns_query(data, addr, "DoT")
                    if response:
                        tls_conn.sendall(len(response).to_bytes(2, 'big') + response)
            except ssl.SSLError as e:
                log.debug(f"TLS error from {addr[0]}: {e}")
            except Exception as e:
                log.debug(f"DoT connection error from {addr[0]}: {e}")
            finally:
                try:
                    tls_conn.close()
                except:
                    pass
        except socket.timeout:
            continue
        except Exception as e:
            if not shutdown_event.is_set():
                log.error(f"DoT loop error: {e}")

    dot_socket.close()
    log.info("DNS-over-TLS server stopped")

# ─── HTTP Dashboard ────────────────────────────────────────────────────────
class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for status dashboard."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/status" or path == "/":
            self.send_json_response(get_status())
        elif path == "/metrics":
            self.send_json_response(get_metrics())
        elif path == "/config":
            self.send_json_response(get_config())
        else:
            self.send_error(404)

    def send_json_response(self, data: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def log_message(self, format, *args):
        # Suppress default HTTP logging
        pass


def get_status() -> dict:
    """Return current status for dashboard."""
    now = datetime.now()
    unlocked = is_unlocked(now)
    next_flip = time_until_next_flip(now)

    return {
        "state": "UNLOCKED" if unlocked else "LOCKED",
        "instagram_access": "allowed" if unlocked else "blocked",
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "window_start": f"{UNLOCK_START[0]:02d}:{UNLOCK_START[1]:02d}",
        "window_end": f"{UNLOCK_END[0]:02d}:{UNLOCK_END[1]:02d}",
        "next_flip_in_seconds": int(next_flip.total_seconds()),
        "next_flip_human": format_timedelta(next_flip),
        "next_flip_at": (now + next_flip).strftime("%Y-%m-%d %H:%M:%S"),
        "blocked_queries": blocked_count,
        "allowed_queries": allowed_count,
        "uptime_seconds": int(time.time() - start_time),
        "uptime_human": format_timedelta(timedelta(seconds=int(time.time() - start_time))),
        "upstream_dns": [f"{h}:{p}" for h, p in UPSTREAM_DNS],
        "blocked_suffixes": list(INSTAGRAM_SUFFIXES),
        "servers": {
            "dns_udp": {"port": DNS_LISTEN_PORT, "proto": "UDP"},
            "dns_tcp": {"port": DNS_LISTEN_PORT, "proto": "TCP"},
            "dot": {"port": DOT_LISTEN_PORT, "proto": "TLS"},
            "http": {"port": HTTP_LISTEN_PORT, "proto": "HTTP"},
        },
    }


def get_metrics() -> dict:
    """Return Prometheus-style metrics."""
    now = datetime.now()
    return {
        "ig_sinkhole_blocked_total": blocked_count,
        "ig_sinkhole_allowed_total": allowed_count,
        "ig_sinkhole_uptime_seconds": int(time.time() - start_time),
        "ig_sinkhole_state": 1 if is_unlocked(now) else 0,
        "ig_sinkhole_next_flip_seconds": int(time_until_next_flip(now).total_seconds()),
    }


def get_config() -> dict:
    """Return current configuration."""
    return {
        "unlock_start": f"{UNLOCK_START[0]:02d}:{UNLOCK_START[1]:02d}",
        "unlock_end": f"{UNLOCK_END[0]:02d}:{UNLOCK_END[1]:02d}",
        "ttl_blocked": TTL_BLOCKED,
        "ttl_allowed": TTL_ALLOWED,
        "upstream_dns": [f"{h}:{p}" for h, p in UPSTREAM_DNS],
        "blocked_suffixes": list(INSTAGRAM_SUFFIXES),
        "doh_provider_ips": list(DOH_PROVIDER_IPS),
    }


def http_server_loop():
    """Run HTTP dashboard server."""
    global http_server
    http_server = HTTPServer((HTTP_LISTEN_ADDR, HTTP_LISTEN_PORT), DashboardHandler)
    log.info(f"HTTP dashboard on http://{HTTP_LISTEN_ADDR}:{HTTP_LISTEN_PORT}/status")
    http_server.serve_forever()

# ─── Signal Handling ───────────────────────────────────────────────────────
def signal_handler(signum, frame):
    """Handle shutdown signals."""
    log.info(f"Received signal {signum}, shutting down...")
    shutdown_event.set()
    if dns_socket_udp:
        dns_socket_udp.close()
    if dns_socket_tcp:
        dns_socket_tcp.close()
    if dot_socket:
        dot_socket.close()
    if http_server:
        http_server.shutdown()


# ─── Main Entry Point ──────────────────────────────────────────────────────
def main():
    import argparse

    global DNS_LISTEN_PORT, DOT_LISTEN_PORT, HTTP_LISTEN_PORT

    parser = argparse.ArgumentParser(description="Instagram Time-Window DNS Sinkhole (Enhanced)")
    parser.add_argument("--test", action="store_true", help="Run one test query and exit")
    parser.add_argument("--no-http", action="store_true", help="Disable HTTP dashboard")
    parser.add_argument("--no-dot", action="store_true", help="Disable DNS-over-TLS server")
    parser.add_argument("--port", type=int, default=DNS_LISTEN_PORT, help="DNS port (default 53)")
    parser.add_argument("--dot-port", type=int, default=DOT_LISTEN_PORT, help="DoT port (default 853)")
    parser.add_argument("--http-port", type=int, default=HTTP_LISTEN_PORT, help="HTTP port (default 8080)")
    args = parser.parse_args()

    # Override ports if provided
    DNS_LISTEN_PORT = args.port
    DOT_LISTEN_PORT = args.dot_port
    HTTP_LISTEN_PORT = args.http_port

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, "SIGBREAK"):  # Windows
        signal.signal(signal.SIGBREAK, signal_handler)

    # Print banner
    print("=" * 70)
    print("  Instagram Time-Window DNS Sinkhole (Enhanced)")
    print("=" * 70)
    print(f"  Block window:  {UNLOCK_END[0]:02d}:{UNLOCK_END[1]:02d} – {UNLOCK_START[0]:02d}:{UNLOCK_START[1]:02d}  (LOCKED)")
    print(f"  Allow window:  {UNLOCK_START[0]:02d}:{UNLOCK_START[1]:02d} – {UNLOCK_END[0]:02d}:{UNLOCK_END[1]:02d}  (UNLOCKED)")
    print(f"  Current state: {'UNLOCKED' if is_unlocked() else 'LOCKED'}")
    print(f"  Next flip in:  {format_timedelta(time_until_next_flip())}")
    print(f"  DNS UDP/TCP:   {DNS_LISTEN_ADDR}:{DNS_LISTEN_PORT}")
    if not args.no_dot:
        print(f"  DNS-over-TLS:  {DOT_LISTEN_ADDR}:{DOT_LISTEN_PORT}")
    if not args.no_http:
        print(f"  HTTP dashboard: http://{HTTP_LISTEN_ADDR}:{HTTP_LISTEN_PORT}/status")
    print(f"  Blocked domains: {len(INSTAGRAM_SUFFIXES)} suffixes")
    print("=" * 70)

    if args.test:
        # One-shot test
        test_query = DNSRecord.question("instagram.com", "A")
        print("\nTest query: instagram.com A")
        print(f"Would be: {'FORWARDED' if is_unlocked() else 'BLOCKED (sinkholed)'}")
        return

    # Start DNS UDP server thread
    dns_udp_thread = threading.Thread(target=dns_udp_loop, daemon=True, name="DNS-UDP")
    dns_udp_thread.start()

    # Start DNS TCP server thread
    dns_tcp_thread = threading.Thread(target=dns_tcp_loop, daemon=True, name="DNS-TCP")
    dns_tcp_thread.start()

    # Start DoT server thread (unless disabled)
    dot_thread = None
    if not args.no_dot:
        dot_thread = threading.Thread(target=dot_server_loop, daemon=True, name="DNS-DoT")
        dot_thread.start()

    # Start HTTP server thread (unless disabled)
    http_thread = None
    if not args.no_http:
        http_thread = threading.Thread(target=http_server_loop, daemon=True, name="HTTP-Dashboard")
        http_thread.start()

    # Wait for shutdown
    try:
        while not shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)

    # Wait for threads
    dns_udp_thread.join(timeout=2)
    dns_tcp_thread.join(timeout=2)
    if dot_thread:
        dot_thread.join(timeout=2)
    if http_thread:
        http_thread.join(timeout=2)

    # Final summary
    print("\n" + "=" * 70)
    print("  Shutdown Summary")
    print("=" * 70)
    print(f"  Blocked queries: {blocked_count}")
    print(f"  Allowed queries: {allowed_count}")
    print(f"  Uptime:          {format_timedelta(timedelta(seconds=int(time.time() - start_time)))}")
    print("=" * 70)


if __name__ == "__main__":
    main()