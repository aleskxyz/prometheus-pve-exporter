"""
Tests for the helpers shared by the exporter modules.
"""

import unittest

from pve_exporter.util import IdleExpiry, parse_bool


class ParseBoolTest(unittest.TestCase):
    """One spelling of booleans for PVE, URL parameters and the environment."""

    def test_values(self):
        for value in (1, '1', 'true', 'Yes', 'on', ' 1 ', True, 2, 0.5):
            self.assertTrue(parse_bool(value), value)
        for value in (0, '0', 'false', 'NO', 'off', False, 0.0):
            self.assertFalse(parse_bool(value, default=True), value)

    def test_missing_is_default(self):
        for value in (None, '', '  '):
            self.assertFalse(parse_bool(value))
            self.assertTrue(parse_bool(value, default=True))


class IdleExpiryTest(unittest.TestCase):
    """Keys not used for longer than idle_seconds expire."""

    def test_other_keys_expire(self):
        expiry = IdleExpiry(idle_seconds=10)
        expiry.use('a', 0)
        expiry.use('b', 5)
        self.assertEqual(expiry.use('b', 11), ['a'])
        self.assertEqual(expiry.use('b', 20), [])

    def test_key_returning_after_idle_expires(self):
        """Its state is older than idle_seconds, so it is returned too."""
        expiry = IdleExpiry(idle_seconds=10)
        expiry.use('a', 0)
        self.assertEqual(expiry.use('a', 11), ['a'])
        # and it is in use again from then on
        self.assertEqual(expiry.use('a', 15), [])


if __name__ == '__main__':
    unittest.main()
