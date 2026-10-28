"""
Tests for the configuration of the exporter.
"""

import unittest

from pve_exporter.config import config_from_env, config_from_yaml
from pve_exporter.util import parse_bool


class ConfigFromYamlTest(unittest.TestCase):
    """Modules of the config file."""

    def test_modules(self):
        config = config_from_yaml({
            'default': {'user': 'u@pve', 'token_name': 't', 'token_value': 'v'},
            'pbs': {'user': 'v@pve', 'password': 'p', 'allow_list': [' PVE1 ', 'pve2']},
        })
        self.assertTrue(config.valid)
        self.assertEqual(dict(config['default']),
                         {'user': 'u@pve', 'token_name': 't', 'token_value': 'v'})
        # allow_list is no API parameter
        self.assertNotIn('allow_list', config['pbs'])
        self.assertIsNone(config['default'].allow_list)
        self.assertTrue(config['default'].allows('anything'))
        self.assertTrue(config['pbs'].allows('pve1'))
        self.assertFalse(config['pbs'].allows('pve3'))

    def test_invalid(self):
        for yaml in (None, [], {}, {'default': 'x'}, {'default': {}},
                     {'default': {'user': 'u', 'allow_list': []}},
                     {'default': {'user': 'u', 'allow_list': ['']}},
                     {'default': {'user': 'u', 'allow_list': 'pve1'}}):
            with self.subTest(yaml=yaml):
                self.assertFalse(config_from_yaml(yaml).valid)


class ConfigFromEnvTest(unittest.TestCase):
    """One module from environment variables."""

    def test_module(self):
        config = config_from_env({'PVE_USER': 'u@pve', 'PVE_PASSWORD': 'p',
                                  'PVE_MODULE': 'm', 'PVE_ALLOW_LIST': 'pve1, ,pve2'})
        self.assertTrue(config.valid)
        self.assertEqual(dict(config['m']), {'user': 'u@pve', 'password': 'p'})
        self.assertEqual(config['m'].allow_list, frozenset({'pve1', 'pve2'}))

    def test_empty_allow_list(self):
        self.assertFalse(config_from_env({'PVE_USER': 'u', 'PVE_ALLOW_LIST': ' , '}).valid)

    def test_verify_ssl(self):
        for value, expected in (('0', False), ('false', False), ('No', False), ('off', False),
                                ('1', True), ('true', True), ('', True)):
            with self.subTest(value=value):
                config = config_from_env({'PVE_USER': 'u', 'PVE_VERIFY_SSL': value})
                self.assertIs(config['default']['verify_ssl'], expected)


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


if __name__ == '__main__':
    unittest.main()
