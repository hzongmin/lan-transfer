import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery import DeviceRegistry, make_beacon, parse_beacon


class DiscoveryTest(unittest.TestCase):
    def test_beacon_round_trip(self):
        beacon = make_beacon("abc", "Office PC", "http://192.168.1.9:8765")
        parsed = parse_beacon(beacon, "192.168.1.9", self_device_id="self")

        self.assertEqual(parsed.device_id, "abc")
        self.assertEqual(parsed.name, "Office PC")
        self.assertEqual(parsed.url, "http://192.168.1.9:8765")

    def test_parse_ignores_self_beacon(self):
        beacon = make_beacon("self", "Me", "http://192.168.1.2:8765")

        self.assertIsNone(parse_beacon(beacon, "192.168.1.2", self_device_id="self"))

    def test_parse_rejects_invalid_json(self):
        self.assertIsNone(parse_beacon(b"not json", "192.168.1.2", self_device_id="self"))

    def test_registry_expires_old_devices(self):
        now = [1000.0]
        registry = DeviceRegistry(ttl_seconds=10, clock=lambda: now[0])
        registry.update(parse_beacon(make_beacon("a", "A", "http://192.168.1.3:8765"), "192.168.1.3", "self"))

        now[0] += 11

        self.assertEqual(registry.active_devices(), [])


if __name__ == "__main__":
    unittest.main()
