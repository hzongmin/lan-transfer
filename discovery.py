import json
import socket
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional


DISCOVERY_PORT = 47865
BEACON_TYPE = "lan-transfer-v3"


@dataclass(frozen=True)
class DiscoveredDevice:
    device_id: str
    name: str
    url: str
    ip: str
    last_seen: float


def make_beacon(device_id: str, name: str, url: str) -> bytes:
    return json.dumps(
        {
            "type": BEACON_TYPE,
            "deviceId": device_id,
            "name": name,
            "url": url,
        }
    ).encode("utf-8")


def parse_beacon(data: bytes, sender_ip: str, self_device_id: str, now: Optional[float] = None) -> Optional[DiscoveredDevice]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if payload.get("type") != BEACON_TYPE:
        return None

    device_id = str(payload.get("deviceId") or "")
    if not device_id or device_id == self_device_id:
        return None

    name = str(payload.get("name") or "Unknown PC")[:80]
    url = str(payload.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return None

    return DiscoveredDevice(
        device_id=device_id,
        name=name,
        url=url,
        ip=sender_ip,
        last_seen=time.time() if now is None else now,
    )


class DeviceRegistry:
    def __init__(self, ttl_seconds: int = 20, clock: Callable[[], float] = time.time) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._devices: dict[str, DiscoveredDevice] = {}
        self._lock = threading.RLock()

    def update(self, device: Optional[DiscoveredDevice]) -> None:
        if device is None:
            return
        with self._lock:
            self._devices[device.device_id] = replace(device, last_seen=self._clock())

    def active_devices(self) -> list[DiscoveredDevice]:
        now = self._clock()
        with self._lock:
            expired = [
                device_id
                for device_id, device in self._devices.items()
                if now - device.last_seen > self.ttl_seconds
            ]
            for device_id in expired:
                self._devices.pop(device_id, None)
            return sorted(self._devices.values(), key=lambda item: item.name.lower())


class DiscoveryService:
    def __init__(
        self,
        device_id: str,
        device_name: str,
        get_url: Callable[[], str],
        registry: Optional[DeviceRegistry] = None,
        port: int = DISCOVERY_PORT,
        interval_seconds: float = 3.0,
    ) -> None:
        self.device_id = device_id
        self.device_name = device_name
        self.get_url = get_url
        self.registry = registry or DeviceRegistry()
        self.port = port
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._send_loop, daemon=True),
            threading.Thread(target=self._receive_loop, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()

    def devices(self) -> list[DiscoveredDevice]:
        return self.registry.active_devices()

    def _send_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not self._stop.is_set():
                data = make_beacon(self.device_id, self.device_name, self.get_url())
                try:
                    sock.sendto(data, ("255.255.255.255", self.port))
                except OSError:
                    pass
                self._stop.wait(self.interval_seconds)
        finally:
            sock.close()

    def _receive_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            sock.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    data, address = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                self.registry.update(parse_beacon(data, address[0], self.device_id))
        except OSError:
            return
        finally:
            sock.close()
