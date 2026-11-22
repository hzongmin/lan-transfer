import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from network_utils import NetworkAddress, choose_preferred_address


class NetworkUtilsTest(unittest.TestCase):
    def test_prefers_wifi_lan_over_vpn_default_route(self):
        candidates = [
            NetworkAddress("10.8.0.2", "WireGuard Tunnel"),
            NetworkAddress("192.168.31.88", "Wi-Fi"),
        ]

        preferred = choose_preferred_address(candidates)

        self.assertEqual(preferred.ip, "192.168.31.88")

    def test_ignores_loopback_when_lan_address_exists(self):
        candidates = [
            NetworkAddress("127.0.0.1", "Loopback"),
            NetworkAddress("192.168.1.20", "Ethernet"),
        ]

        preferred = choose_preferred_address(candidates)

        self.assertEqual(preferred.ip, "192.168.1.20")

    def test_returns_loopback_when_no_other_address_exists(self):
        preferred = choose_preferred_address([NetworkAddress("127.0.0.1", "Loopback")])

        self.assertEqual(preferred.ip, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
