import ipaddress
import re
import socket
import subprocess
from dataclasses import dataclass


_VIRTUAL_HINTS = (
    "vpn",
    "wireguard",
    "wintun",
    "tap",
    "openvpn",
    "tailscale",
    "zerotier",
    "clash",
    "tun",
    "virtual",
    "vmware",
    "virtualbox",
    "hyper-v",
    "vethernet",
)
_LAN_HINTS = ("wi-fi", "wifi", "wlan", "wireless", "ethernet", "以太网", "无线")


@dataclass(frozen=True)
class NetworkAddress:
    ip: str
    adapter: str

    @property
    def label(self) -> str:
        return f"{self.ip}  ({self.adapter})"


def discover_addresses() -> list[NetworkAddress]:
    addresses = _discover_from_ipconfig()
    if not addresses:
        addresses = _discover_from_socket()
    return _dedupe(addresses) or [NetworkAddress("127.0.0.1", "Loopback")]


def choose_preferred_address(addresses: list[NetworkAddress]) -> NetworkAddress:
    return sorted(addresses, key=_address_score, reverse=True)[0]


def _discover_from_ipconfig() -> list[NetworkAddress]:
    try:
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="ignore",
            timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    addresses: list[NetworkAddress] = []
    adapter = "Unknown"
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if raw_line and not raw_line[0].isspace() and line.endswith(":"):
            adapter = line[:-1]
            continue
        match = re.search(r"IPv4[^\:]*:\s*([0-9]+(?:\.[0-9]+){3})", line, re.IGNORECASE)
        if match:
            addresses.append(NetworkAddress(match.group(1), adapter))
    return addresses


def _discover_from_socket() -> list[NetworkAddress]:
    names = {socket.gethostname(), socket.getfqdn()}
    addresses: list[NetworkAddress] = []
    for name in names:
        try:
            infos = socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            continue
        for info in infos:
            addresses.append(NetworkAddress(info[4][0], name))
    return addresses


def _dedupe(addresses: list[NetworkAddress]) -> list[NetworkAddress]:
    seen = set()
    result = []
    for address in addresses:
        if address.ip in seen:
            continue
        seen.add(address.ip)
        result.append(address)
    return result


def _address_score(address: NetworkAddress) -> int:
    try:
        ip = ipaddress.ip_address(address.ip)
    except ValueError:
        return -100

    name = address.adapter.lower()
    score = 0
    if ip.is_loopback:
        score -= 100
    if ip.is_private:
        score += 50
    if str(ip).startswith("169.254."):
        score -= 40
    if any(hint in name for hint in _LAN_HINTS):
        score += 40
    if any(hint in name for hint in _VIRTUAL_HINTS):
        score -= 80
    if str(ip).startswith(("192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
        score += 15
    return score
