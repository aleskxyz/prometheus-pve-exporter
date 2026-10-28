"""
Tests for the creation time of Proxmox VE guests.
"""

import itertools
import unittest
from unittest import mock

from fakes import FakeAPI, samples, series

from pve_exporter.collector import guest
from pve_exporter.collector.guest import CreatedCache, GuestCreatedCollector
from pve_exporter.collector.scrape_api import ScrapeAPI, TargetUnreachable

VMS = [
    {'type': 'qemu', 'id': 'qemu/100', 'vmid': 100, 'node': 'pve1'},
    {'type': 'qemu', 'id': 'qemu/101', 'vmid': 101, 'node': 'pve2'},
    {'type': 'lxc', 'id': 'lxc/200', 'vmid': 200, 'node': 'pve2'},
]


class GuestCreatedCollectorTest(unittest.TestCase):
    """Creation time of every qemu guest, read once per VMID."""

    def setUp(self):
        self.cache = CreatedCache()

    def collector(self, api, scope='dc1'):
        """A collector of scope with the cache of this test."""
        return GuestCreatedCollector(api, scope, self.cache)

    def api(self, reads):
        """A fake PVE with VMS; qemu/101 has no meta option. Config reads are counted."""
        def config(vmid, meta):
            def answer(_params):
                reads.append(vmid)
                return {'meta': meta} if meta else {}
            return answer
        return FakeAPI({
            'cluster/resources': VMS,
            'nodes/pve1/qemu/100/config': config(100, 'creation-qemu=9.0.2,ctime=1700000000'),
            'nodes/pve2/qemu/101/config': config(101, None),
        })

    def test_every_qemu_guest(self):
        reads = []
        result = samples(self.collector(self.api(reads)).collect())
        # also guests in no backup job; LXC has no creation time
        self.assertEqual(series(result, 'pve_guest_created_timestamp_seconds'),
                         {frozenset({('id', 'qemu/100')}): 1700000000})
        self.assertEqual(sorted(reads), [100, 101])

    def test_read_once(self):
        reads = []
        api = self.api(reads)
        list(self.collector(api).collect())
        list(self.collector(api).collect())
        self.assertEqual(sorted(reads), [100, 101])

    def test_unreadable_config_is_read_again(self):
        reads = []
        api = self.api(reads)
        api.routes['nodes/pve2/qemu/101/config'] = RuntimeError('500')
        collector = self.collector(api)
        result = samples(collector.collect())
        self.assertEqual(series(result, 'pve_guest_created_timestamp_seconds'),
                         {frozenset({('id', 'qemu/100')}): 1700000000})
        self.assertIn('101', collector.partial_failure)
        api.routes['nodes/pve2/qemu/101/config'] = lambda _params: {}
        collector = self.collector(api)
        list(collector.collect())
        self.assertIsNone(collector.partial_failure)
        self.assertEqual(reads, [100])

    def test_budget_used_up(self):
        reads = []
        collector = self.collector(self.api(reads))
        with mock.patch.object(guest, 'CREATED_BUDGET_SECONDS', -1):
            list(collector.collect())
        self.assertEqual(reads, [])
        self.assertIn('2 qemu guests not read yet', collector.partial_failure)

    def test_guests_of_offline_nodes_are_skipped(self):
        reads = []
        api = self.api(reads)
        api.routes['cluster/resources'] = [
            dict(VMS[0], status='unknown'), dict(VMS[1], node=None)]
        collector = self.collector(api)
        list(collector.collect())
        self.assertEqual(reads, [])
        self.assertIsNone(collector.partial_failure)

    def test_guest_of_offline_node_keeps_its_creation_time(self):
        reads = []
        api = self.api(reads)
        list(self.collector(api).collect())
        # an unknown guest has no name
        api.routes['cluster/resources'] = [
            {'type': 'qemu', 'id': 'qemu/100', 'vmid': 100, 'node': 'pve1', 'status': 'unknown'}, VMS[1]]
        result = samples(self.collector(api).collect())
        self.assertEqual(series(result, 'pve_guest_created_timestamp_seconds'),
                         {frozenset({('id', 'qemu/100')}): 1700000000})
        self.assertEqual(sorted(reads), [100, 101])

    def test_vmid_with_another_name_is_read_again(self):
        reads = []
        api = self.api(reads)
        list(self.collector(api).collect())
        api.routes['cluster/resources'] = [dict(VMS[0], name='new'), VMS[1]]
        list(self.collector(api).collect())
        self.assertEqual(sorted(reads), [100, 100, 101])

    def test_reused_vmid_is_read_again(self):
        reads = []
        api = self.api(reads)
        list(self.collector(api).collect())
        api.routes['cluster/resources'] = VMS[1:]
        list(self.collector(api).collect())
        api.routes['cluster/resources'] = VMS
        list(self.collector(api).collect())
        self.assertEqual(sorted(reads), [100, 100, 101])

    def test_scopes_keep_their_creation_times(self):
        """Modules whose credentials see different guests do not evict each other's guests."""
        reads = []
        admin = self.api(reads)
        limited = self.api(reads)
        limited.routes['cluster/resources'] = VMS[:1]
        for _ in range(3):
            list(self.collector(admin, ('admin', 'pve1')).collect())
            list(self.collector(limited, ('limited', 'pve1')).collect())
        # each scope reads each of its guests once
        self.assertEqual(sorted(reads), [100, 100, 101])

    def test_unreachable_target_fails_the_collector_at_once(self):
        """The first guest that can not reach the target ends the reads, with no log per guest."""
        reads = []

        def unreachable(_params):
            reads.append(1)
            raise ConnectionError('refused')
        vms = [{'type': 'qemu', 'id': f'qemu/{vmid}', 'vmid': vmid, 'node': 'pve1'}
               for vmid in range(100, 400)]
        routes = {f'nodes/pve1/qemu/{vm["vmid"]}/config': unreachable for vm in vms}
        routes['cluster/resources'] = vms
        with self.assertNoLogs('gunicorn.error'), self.assertRaises(TargetUnreachable):
            list(self.collector(ScrapeAPI(FakeAPI(routes))).collect())
        # tried once and once more, then given up
        self.assertEqual(len(reads), 2)

    def test_failed_guests_are_read_after_new_ones(self):
        reads = []
        api = self.api(reads)
        api.routes['nodes/pve2/qemu/101/config'] = lambda _params: reads.append(101) or 1 / 0
        list(self.collector(api).collect())
        # a new guest with a higher VMID comes before the guest that failed
        api.routes['cluster/resources'] = VMS + [dict(VMS[0], id='qemu/150', vmid=150)]
        api.routes['nodes/pve1/qemu/150/config'] = lambda _params: reads.append(150) or {}
        list(self.collector(api).collect())
        self.assertEqual(reads, [100, 101, 150, 101])

    def test_failing_guests_do_not_starve_the_others(self):
        """With a budget of one read per scrape, a healthy guest is read once each failing one was tried."""
        reads = []

        def failing(vmid):
            return lambda _params: reads.append(vmid) or 1 / 0
        vms = [{'type': 'qemu', 'id': f'qemu/{vmid}', 'vmid': vmid, 'node': 'pve1'}
               for vmid in (100, 101, 102, 200)]
        routes = {f'nodes/pve1/qemu/{vmid}/config': failing(vmid) for vmid in (100, 101, 102)}
        routes['nodes/pve1/qemu/200/config'] = lambda _params: reads.append(200) or {'meta': 'ctime=5'}
        routes['cluster/resources'] = vms
        api = FakeAPI(routes)
        # each call of the clock is one second later
        with mock.patch.object(guest, 'CREATED_BUDGET_SECONDS', 1.5), \
                mock.patch('time.monotonic', side_effect=itertools.count()):
            results = [samples(self.collector(api).collect()) for _ in range(5)]
        self.assertEqual(reads, [100, 101, 102, 200, 100])
        self.assertEqual(series(results[3], 'pve_guest_created_timestamp_seconds'),
                         {frozenset({('id', 'qemu/200')}): 5})

    def test_failed_guests_take_turns(self):
        """A guest that keeps failing slowly does not keep the other failed guests from a retry."""
        reads, clock, slow = [], itertools.count(), []

        def guest_100(_params):
            reads.append(100)
            if slow:  # uses up the read budget
                for _ in range(10):
                    next(clock)
            raise RuntimeError('596 timeout')

        def guest_101(_params):
            reads.append(101)
            if not slow:
                raise RuntimeError('500')
            return {'meta': 'ctime=5'}
        vms = [{'type': 'qemu', 'id': f'qemu/{vmid}', 'vmid': vmid, 'node': 'pve1'} for vmid in (100, 101)]
        api = FakeAPI({'cluster/resources': vms, 'nodes/pve1/qemu/100/config': guest_100,
                       'nodes/pve1/qemu/101/config': guest_101})
        with mock.patch('time.monotonic', side_effect=lambda: next(clock)):
            list(self.collector(api).collect())
            slow.append(True)
            results = [samples(self.collector(api).collect()) for _ in range(2)]
        # both failed; 100 failed longest ago and uses up the budget; then 101
        # comes first, is read, and leaves time to try 100 once more
        self.assertEqual(reads, [100, 101, 100, 101, 100])
        self.assertEqual(series(results[1], 'pve_guest_created_timestamp_seconds'),
                         {frozenset({('id', 'qemu/101')}): 5})

    def test_scope_returning_after_idle_is_read_again(self):
        reads = []
        api = self.api(reads)
        self.cache = CreatedCache(idle_seconds=10)
        # clock calls of a scrape of two guests: state, budget, one per read
        with mock.patch('time.monotonic', side_effect=[0, 1, 2, 3, 100, 101, 102, 103]):
            list(self.collector(api).collect())
            list(self.collector(api).collect())
        self.assertEqual(reads, [100, 101, 100, 101])

    def test_failures_are_summarized_not_logged_per_guest(self):
        vms = [{'type': 'qemu', 'id': f'qemu/{vmid}', 'vmid': vmid, 'node': 'pve1'}
               for vmid in range(100, 130)]
        routes = {f'nodes/pve1/qemu/{vm["vmid"]}/config': RuntimeError('595 no route')
                  for vm in vms}
        routes['cluster/resources'] = vms
        collector = self.collector(FakeAPI(routes))
        with self.assertNoLogs('gunicorn.error', level='WARNING'):
            list(collector.collect())
        self.assertEqual(
            collector.partial_failure,
            'configs of qemu guests 100, 101, 102, 103, 104, 105, 106, 107, 108, 109 '
            'and 20 more unreadable (last error: 595 no route)')

    def test_idle_scopes_are_forgotten(self):
        reads = []
        api = self.api(reads)
        self.cache = CreatedCache(idle_seconds=6)
        # each call of the clock is one second later; a scrape calls it 2 to 4 times
        with mock.patch('time.monotonic', side_effect=itertools.count()):
            list(self.collector(api, 'old').collect())
            list(self.collector(api, 'new').collect())
            list(self.collector(api, 'new').collect())
            list(self.collector(api, 'old').collect())
        # old was idle for longer than 6 s when new was used again, and is read again
        self.assertEqual(reads, [100, 101, 100, 101, 100, 101])
        self.assertEqual(set(self.cache._created), {'new', 'old'})  # pylint: disable=protected-access


if __name__ == '__main__':
    unittest.main()
