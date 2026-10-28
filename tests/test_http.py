"""
Tests for the HTTP API of the exporter.
"""

import logging
import subprocess
import sys
import unittest
from unittest import mock

from prometheus_client import REGISTRY
from werkzeug.test import Client

from pve_exporter import collector
from pve_exporter.collector import CollectorsOptions
from pve_exporter.config import config_from_yaml
from pve_exporter.collector.scrape_api import TargetUnreachable
from pve_exporter.http import PveExporterApplication

CONFIG = config_from_yaml({
    'default': {'user': 'u@pve', 'token_name': 't', 'token_value': 'v'},
    'limited': {'user': 'v@pve', 'token_name': 't', 'token_value': 'v', 'allow_list': ['pve1']},
})


def errors(module):
    """pve_request_errors_total of module, None if it has no series."""
    return REGISTRY.get_sample_value('pve_request_errors_total', {'module': module})


class PveTest(unittest.TestCase):
    """The /pve route."""

    @classmethod
    def setUpClass(cls):
        # The metrics of the application are registered once per process.
        cls.app = PveExporterApplication(CONFIG, CollectorsOptions(), logging.getLogger('test'))
        cls.client = Client(cls.app)

    def test_scrape(self):
        with mock.patch.object(collector, 'collect_pve', return_value=b'pve_x 1\n') as collect:
            response = self.client.get('/pve?module=limited&target=PVE1&node=0')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b'pve_x 1\n')
        args, kwargs = collect.call_args
        self.assertEqual(args[1:4], ('pve1', True, False))
        self.assertEqual(kwargs, {'module': 'limited'})

    def test_url_flags(self):
        for value, expected in (('1', True), ('0', False), ('false', False), ('', False),
                                ('no', False)):
            with self.subTest(value=value), mock.patch.object(
                    collector, 'collect_pve', return_value=b'') as collect:
                self.client.get(f'/pve?cluster={value}')
                self.assertIs(collect.call_args.args[2], expected)

    def test_target_not_allowed(self):
        with mock.patch.object(collector, 'collect_pve') as collect:
            response = self.client.get('/pve?module=limited&target=pve2')
        self.assertEqual(response.status_code, 403)
        collect.assert_not_called()

    def test_target_down_is_logged_without_traceback(self):
        with mock.patch.object(collector, 'collect_pve', side_effect=TargetUnreachable('refused')), \
                self.assertLogs('test', level='WARNING') as logs:
            self.assertEqual(self.client.get('/pve').status_code, 500)
        self.assertEqual([(r.levelname, r.exc_info) for r in logs.records], [('WARNING', None)])
        self.assertIn('refused', logs.output[0])

    def test_unknown_module(self):
        response = self.client.get('/pve?module=nope')
        self.assertEqual(response.status_code, 400)

    def test_error_counts_only_modules_of_the_config(self):
        before = errors('default')
        with mock.patch.object(collector, 'collect_pve', side_effect=RuntimeError('boom')), \
                self.assertLogs('test', level='ERROR'):
            self.assertEqual(self.client.get('/pve').status_code, 500)
        self.assertEqual(errors('default'), before + 1)
        # a failing view with a module of the request makes no series
        with mock.patch.object(self.app, 'on_index', side_effect=RuntimeError('boom')), \
                self.assertLogs('test', level='ERROR'):
            self.assertEqual(self.client.get('/?module=x%0Ay').status_code, 500)
        self.assertIsNone(errors('x\ny'))


# Imports http before scrape_metrics, as a gunicorn config or another entry
# point might, and scrapes a target that fails.
TARGET_METRICS_SCRIPT = '''
import logging
from unittest import mock
from werkzeug.test import Client
from pve_exporter.http import PveExporterApplication
from pve_exporter import scrape_metrics
from pve_exporter.collector import CollectorsOptions
from pve_exporter.config import config_from_yaml
from prometheus_client import REGISTRY

scrape_metrics.TARGET_METRICS_ENABLED = True
config = config_from_yaml({'default': {'user': 'u', 'token_name': 't', 'token_value': 'v'}})
app = PveExporterApplication(config, CollectorsOptions(), logging.getLogger('quiet'))
logging.getLogger('quiet').disabled = True
with mock.patch('pve_exporter.collector.ProxmoxAPI', side_effect=RuntimeError('x')):
    Client(app).get('/pve?target=wrapped-host')
print(REGISTRY.get_sample_value('pve_scrape_target_errors_total',
                                {'target': 'wrapped-host', 'cluster': '1', 'node': '1'}))
'''


class TargetMetricsTest(unittest.TestCase):
    """Target metrics are recorded whichever module was imported first."""

    def test_http_imported_first(self):
        result = subprocess.run([sys.executable, '-c', TARGET_METRICS_SCRIPT],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '1.0')


if __name__ == '__main__':
    unittest.main()
