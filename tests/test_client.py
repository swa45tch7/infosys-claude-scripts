from __future__ import annotations

import unittest

from lm_infosys.client import sign


class SignTests(unittest.TestCase):
    def test_signature_is_stable(self) -> None:
        value = sign("secret-key", "GET", "1700000000000", "", "/device/devices")
        self.assertEqual(
            value,
            sign("secret-key", "GET", "1700000000000", "", "/device/devices"),
        )
        self.assertNotEqual(
            value,
            sign("other-key", "GET", "1700000000000", "", "/device/devices"),
        )
        # base64 of hex HMAC — always ASCII without whitespace
        self.assertRegex(value, r"^[A-Za-z0-9+/=]+$")


if __name__ == "__main__":
    unittest.main()
