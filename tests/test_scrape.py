"""
Tests for a scrape: the collector registry, collector isolation and the PVE
API as one scrape uses it.
"""

import itertools
import unittest
from unittest import mock

import requests
from prometheus_client import REGISTRY
from prometheus_client.core import GaugeMetricFamily

from fakes import FakeAPI, parsed, series

from pve_exporter import scrape_metrics
from pve_exporter.collector import (
    COLLECTORS,
    CollectorsOptions,
    GuardedCollector,
    collect_pve,
)
from pve_exporter.collector import backup, guest
from pve_exporter.collector.scrape_api import ScrapeAPI, TargetUnreachable, endpoint
from pve_exporter.util import IdleExpiry

NONE = {spec.name: False for spec in COLLECTORS}

RESOURCES = [
    {'type': 'node', 'id': 'node/pve1', 'node': 'pve1'},
    {'type': 'qemu', 'id': 'qemu/100', 'vmid': 100, 'node': 'pve1', 'status': 'running'},
    {'type': 'lxc', 'id': 'lxc/200', 'vmid': 200, 'node': 'pve1', 'status': 'stopped'},
    {'type': 'storage', 'id': 'storage/pve1/local', 'node': 'pve1', 'status': 'available'},
]


def options(**enabled):
    """Options with only the given collectors enabled."""
    return CollectorsOptions(**dict(NONE, **enabled))


def success(text):
    """{collector: pve_collector_success} of a scrape."""
    return {dict(labels)['collector']: value
            for labels, value in series(parsed(text), 'pve_collector_success').items()}


class CollectPveTest(unittest.TestCase):
    """The registry decides which collectors a scrape runs."""

    def setUp(self):
        patcher = mock.patch.object(guest, 'CREATED', guest.CreatedCache())
        self.created = patcher.start()
        self.addCleanup(patcher.stop)

    def test_url_parameters_select_collectors(self):
        api = FakeAPI({'version': {'version': '9.0'}})
        text = collect_pve({}, 'pve1', False, False, CollectorsOptions(), api=api).decode()
        self.assertEqual(success(text), {})
        text = collect_pve({}, 'pve1', True, False, options(version=True, config=True),
                           api=api).decode()
        self.assertEqual(success(text), {'version': 1})

    def test_resources_are_read_once(self):
        api = FakeAPI({
            'version': {'version': '9.0'},
            'cluster/status': [{'type': 'node', 'id': 'node/pve1', 'name': 'pve1', 'online': 1}],
            'cluster/resources': RESOURCES,
            'nodes/pve1/qemu/100/config': {'meta': 'ctime=1700000000'},
        })
        text = collect_pve({}, 'pve1', True, False,
                           options(status=True, resources=True, guest_created=True),
                           api=api).decode()
        self.assertEqual(success(text), {'status': 1, 'resources': 1, 'guest_created': 1})
        self.assertEqual([request for request in api.requests if request[0] == 'cluster/resources'],
                         [('cluster/resources', {})])
        result = parsed(text)
        self.assertEqual(series(result, 'pve_guest_running'), {
            frozenset({('id', 'qemu/100')}): 1, frozenset({('id', 'lxc/200')}): 0})
        self.assertEqual(series(result, 'pve_storage_available'),
                         {frozenset({('id', 'storage/pve1/local')}): 1})

    def test_caches_are_kept_per_module_and_target(self):
        api = FakeAPI({
            'version': {'version': '9.0'},
            'cluster/resources': RESOURCES,
            'nodes/pve1/qemu/100/config': {},
        })
        collect_pve({}, 'pve1', True, False, options(guest_created=True), module='admin', api=api)
        self.assertEqual(set(self.created._created), {('admin', 'pve1')})  # pylint: disable=protected-access

    def test_failing_shared_request_is_sent_once(self):
        api = FakeAPI({'version': {'version': '9.0'},
                       'cluster/status': RuntimeError('596 read timeout')})
        users = ('status', 'node', 'cluster', 'qdevice', 'backup_tasks', 'subscription',
                 'config', 'replication')
        with self.assertLogs('gunicorn.error', level='ERROR'):
            text = collect_pve({}, 'pve1', True, True, options(**dict.fromkeys(users, True)),
                               api=api).decode()
        self.assertEqual(success(text), dict.fromkeys(users, 0))
        self.assertEqual([path for path, _ in api.requests].count('cluster/status'), 1)

    def test_unreachable_target_fails_the_scrape(self):
        def unreachable(_params):
            raise requests.exceptions.ConnectionError('refused')
        with self.assertRaises(TargetUnreachable):
            collect_pve({}, 'dc1', True, True, CollectorsOptions(),
                        api=FakeAPI({'version': unreachable}))

    def test_target_lost_after_the_version_call(self):
        """Every collector fails at once, logged without a traceback."""
        reads = []

        def unreachable(_params):
            reads.append(1)
            raise requests.exceptions.ConnectionError('refused')
        api = FakeAPI({'version': {'version': '9.0'}, 'cluster/status': unreachable,
                       'cluster/resources': unreachable})
        with self.assertLogs('gunicorn.error') as logs:
            text = collect_pve({}, 'pve1', True, False,
                               options(status=True, node=True, backup_tasks=True),
                               api=api).decode()
        self.assertEqual(success(text), {'status': 0, 'node': 0, 'backup_tasks': 0})
        self.assertEqual(len(reads), 2)
        self.assertEqual([record.levelname for record in logs.records], ['WARNING'] * 3)
        self.assertTrue(all(record.exc_info is None for record in logs.records))


class CollectorIsolationTest(unittest.TestCase):
    """A failing collector only fails itself."""

    def test_failure_is_isolated(self):
        api = FakeAPI({
            'version': {'version': '9.2'},
            'storage': RuntimeError('403 Forbidden'),
            'cluster/backup': [],
        })
        with mock.patch.object(backup, 'SCHEDULES', backup.ScheduleCache()), \
                self.assertLogs('gunicorn.error', level='ERROR'):
            text = collect_pve({}, 'dc1', True, False,
                               options(backup_jobs=True, storage_config=True), api=api).decode()
        self.assertEqual(success(text), {'storage_config': 0, 'backup_jobs': 1})
        self.assertNotIn('pve_storage_config_info', {name for name, _ in parsed(text)})

    def test_partial_failure_fails_the_collector(self):
        class Partial:  # pylint: disable=too-few-public-methods,missing-class-docstring
            partial_failure = 'node pve2 unreadable'

            def collect(self):  # pylint: disable=missing-function-docstring
                yield GaugeMetricFamily('pve_x', 'x', value=1)
        results = {}
        families = GuardedCollector('x', Partial(), results).collect()
        self.assertEqual([family.name for family in families], ['pve_x'])
        self.assertFalse(results['x'][0])


class ScrapeAPITest(unittest.TestCase):
    """One scrape reads each list once and gives up on a target that does not answer."""

    def test_get_is_cached(self):
        reads = []
        api = ScrapeAPI(FakeAPI({'cluster/resources': lambda params: reads.append(params) or []}))
        api.cluster.resources.get(type='vm')
        api.cluster.resources.get(type='vm')
        api.cluster.resources.get()
        self.assertEqual(reads, [{'type': 'vm'}, {}])

    def test_unreachable_target_fails_fast(self):
        reads = []

        def unreachable(_params):
            reads.append(1)
            raise requests.exceptions.ConnectTimeout('timed out')
        api = ScrapeAPI(FakeAPI({'cluster/status': unreachable, 'storage': unreachable}))
        with self.assertRaises(TargetUnreachable) as raised:
            api.cluster.status.get()
        self.assertIsInstance(raised.exception.__cause__, requests.exceptions.ConnectTimeout)
        with self.assertRaises(TargetUnreachable):
            api.storage.get()
        # tried twice, then given up
        self.assertEqual(len(reads), 2)

    def test_connection_error_is_retried_once(self):
        reads = []

        def flaky(_params):
            reads.append(1)
            if len(reads) == 1:
                raise requests.exceptions.ConnectTimeout('timed out')
            return []
        api = ScrapeAPI(FakeAPI({'cluster/status': flaky, 'storage': []}))
        self.assertEqual(api.cluster.status.get(), [])
        self.assertEqual(api.storage.get(), [])

    def test_endpoint(self):
        self.assertEqual(endpoint(('nodes', ('pve1',), 'qemu', ('100',), 'config')),
                         '/nodes/{node}/qemu/{vmid}/config')
        self.assertEqual(endpoint(('cluster', 'backup', ('backup-1',), 'included_volumes')),
                         '/cluster/backup/{id}/included_volumes')
        self.assertEqual(endpoint(('cluster', 'jobs', 'schedule-analyze')),
                         '/cluster/jobs/schedule-analyze')
        self.assertEqual(endpoint(('nodes', ('pve1',), 'storage', ('local',), 'status')),
                         '/nodes/{node}/storage/{storage}/status')
        self.assertEqual(endpoint(('version',)), '/version')

    def test_api_metrics_per_endpoint(self):
        """Requests sent are measured per target and endpoint, not per guest."""
        def config(_params):
            return {}

        def broken(_params):
            raise RuntimeError('500')
        routes = {f'nodes/pve1/qemu/{vmid}/config': config for vmid in (100, 101, 102)}
        routes['storage'] = broken
        api = ScrapeAPI(FakeAPI(routes), 'metrics-target')
        with mock.patch.object(scrape_metrics, 'API_METRICS_ENABLED', True):
            for vmid in (100, 101, 102, 100):
                api.nodes('pve1').qemu(vmid).config.get()
            with self.assertRaises(RuntimeError):
                api.storage.get()

        def value(name, path):
            return REGISTRY.get_sample_value(
                name, {'target': 'metrics-target', 'endpoint': path})
        # the cached fourth read is no request
        self.assertEqual(value('pve_scrape_api_duration_seconds_count',
                               '/nodes/{node}/qemu/{vmid}/config'), 3)
        self.assertEqual(value('pve_scrape_api_errors_total',
                               '/nodes/{node}/qemu/{vmid}/config'), 0)
        self.assertEqual(value('pve_scrape_api_errors_total', '/storage'), 1)
        self.assertEqual(
            {sample.labels['endpoint'] for metric in REGISTRY.collect()
             if metric.name == 'pve_scrape_api_duration_seconds'
             for sample in metric.samples if sample.labels.get('target') == 'metrics-target'},
            {'/nodes/{node}/qemu/{vmid}/config', '/storage'})

    def test_series_of_idle_targets_are_removed(self):
        api = {target: ScrapeAPI(FakeAPI({'version': {}}), target) for target in ('idle-old', 'idle-new')}

        def value(target):
            return REGISTRY.get_sample_value(
                'pve_scrape_api_duration_seconds_count', {'target': target, 'endpoint': '/version'})
        # each request calls the clock once, one second later
        with mock.patch.object(scrape_metrics, 'API_METRICS_ENABLED', True), \
                mock.patch.object(scrape_metrics, '_EXPIRY', IdleExpiry(1.5)), \
                mock.patch('time.monotonic', side_effect=itertools.count()):
            api['idle-old'].version.get()
            self.assertEqual(value('idle-old'), 1)
            api['idle-new'].version.get()
            ScrapeAPI(FakeAPI({'version': {}}), 'idle-new').version.get()
        self.assertIsNone(value('idle-old'))
        self.assertEqual(value('idle-new'), 2)

    def test_api_metrics_disabled(self):
        api = ScrapeAPI(FakeAPI({'version': {}}), 'disabled-target')
        api.version.get()
        self.assertIsNone(REGISTRY.get_sample_value(
            'pve_scrape_api_duration_seconds_count',
            {'target': 'disabled-target', 'endpoint': '/version'}))

    def test_failed_request_is_not_sent_again(self):
        """A slow endpoint costs its timeout once per scrape, not once per collector."""
        def slow(_params):
            raise requests.exceptions.ReadTimeout('read timed out')
        fake = FakeAPI({'cluster/status': slow})
        api = ScrapeAPI(fake)
        for _ in range(3):
            with self.assertRaises(requests.exceptions.ReadTimeout):
                api.cluster.status.get()
        self.assertEqual(len(fake.requests), 1)

    def test_tls_failure_is_not_retried(self):
        """The target answered: a TLS failure neither is retried nor ends the scrape."""
        fake = FakeAPI({'version': requests.exceptions.SSLError('certificate verify failed'),
                        'storage': []})
        api = ScrapeAPI(fake)
        with self.assertRaises(requests.exceptions.SSLError):
            api.version.get()
        self.assertEqual(api.storage.get(), [])
        self.assertEqual([path for path, _ in fake.requests], ['version', 'storage'])

    def test_read_timeout_fails_only_its_request(self):
        def slow(_params):
            raise requests.exceptions.ReadTimeout('read timed out')
        api = ScrapeAPI(FakeAPI({'nodes/pve1/qemu/100/config': slow, 'storage': []}))
        with self.assertRaises(requests.exceptions.ReadTimeout):
            api.nodes('pve1').qemu(100).config.get()
        self.assertEqual(api.storage.get(), [])


if __name__ == '__main__':
    unittest.main()
