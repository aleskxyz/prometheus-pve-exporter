"""
Tests for the collectors of the node that answers the Proxmox VE API.
"""

import unittest

from fakes import FakeAPI, samples, series

from pve_exporter.collector.node import (
    NodeConfigCollector,
    NodeReplicationCollector,
    SubscriptionCollector,
)

CLUSTER_STATUS = [
    {'type': 'cluster', 'id': 'cluster', 'name': 'pvec'},
    {'type': 'node', 'id': 'node/pve2', 'name': 'pve2', 'local': 0},
    {'type': 'node', 'id': 'node/pve1', 'name': 'pve1', 'local': 1},
]


def fake_pve(**routes):
    """A fake PVE whose local node is pve1."""
    return FakeAPI(dict({'cluster/status': CLUSTER_STATUS}, **routes))


class NodeConfigCollectorTest(unittest.TestCase):
    """The onboot option of the guests of the local node."""

    def test_onboot(self):
        pve = fake_pve(**{
            'nodes/pve1/qemu': [{'vmid': 100}, {'vmid': 101}],
            'nodes/pve1/lxc': [{'vmid': 200}],
            'nodes/pve1/qemu/100/config': {'onboot': 1},
            'nodes/pve1/qemu/101/config': {},
            'nodes/pve1/lxc/200/config': {'onboot': '1'},
        })
        result = samples(NodeConfigCollector(pve).collect())
        self.assertEqual(series(result, 'pve_guest_onboot'), {
            frozenset({('id', 'qemu/100'), ('node', 'pve1')}): 1,
            # not set is PVE's default 0
            frozenset({('id', 'qemu/101'), ('node', 'pve1')}): 0,
            frozenset({('id', 'lxc/200'), ('node', 'pve1')}): 1,
        })

    def test_no_local_node(self):
        pve = FakeAPI({'cluster/status': CLUSTER_STATUS[:2]})
        with self.assertRaisesRegex(LookupError, 'no local node'):
            list(NodeConfigCollector(pve).collect())


class NodeReplicationCollectorTest(unittest.TestCase):
    """Replication jobs of the local node and their status."""

    def test_replication(self):
        pve = fake_pve(**{
            'nodes/pve1/replication': [{'id': '100-0', 'type': 'local', 'source': 'pve1',
                                        'target': 'pve2', 'vmtype': 'qemu', 'guest': 100}],
            'nodes/pve1/replication/100-0/status': {'duration': 2.5, 'fail_count': 1,
                                                    'last_sync': 1700000000, 'other': 'x'},
        })
        result = samples(NodeReplicationCollector(pve).collect())
        job = frozenset({('id', '100-0')})
        self.assertEqual(series(result, 'pve_replication_duration_seconds'), {job: 2.5})
        self.assertEqual(series(result, 'pve_replication_failed_syncs'), {job: 1})
        self.assertEqual(series(result, 'pve_replication_last_sync_timestamp_seconds'),
                         {job: 1700000000})
        self.assertEqual(series(result, 'pve_replication_info'), {
            frozenset({('id', '100-0'), ('type', 'local'), ('source', 'node/pve1'),
                       ('target', 'node/pve2'), ('guest', 'qemu/100')}): 1})


class SubscriptionCollectorTest(unittest.TestCase):
    """The subscription of the local node."""

    def test_subscription(self):
        pve = fake_pve(**{'nodes/pve1/subscription': {
            'level': 'c', 'status': 'active', 'nextduedate': '2027-01-15'}})
        result = samples(SubscriptionCollector(pve).collect())
        node = frozenset({('id', 'node/pve1')})
        self.assertEqual(series(result, 'pve_subscription_info'),
                         {frozenset({('id', 'node/pve1'), ('level', 'c')}): 1})
        statuses = {dict(labels)['status']: value
                    for labels, value in series(result, 'pve_subscription_status').items()}
        self.assertEqual(statuses, {'new': 0, 'notfound': 0, 'active': 1, 'invalid': 0,
                                    'expired': 0, 'suspended': 0})
        # midnight UTC, whatever the timezone of the exporter
        self.assertEqual(series(result, 'pve_subscription_next_due_timestamp_seconds'),
                         {node: 1799971200})

    def test_no_subscription(self):
        pve = fake_pve(**{'nodes/pve1/subscription': {'status': 'notfound'}})
        result = samples(SubscriptionCollector(pve).collect())
        self.assertEqual(series(result, 'pve_subscription_info'),
                         {frozenset({('id', 'node/pve1'), ('level', 'unknown')}): 1})
        self.assertEqual(series(result, 'pve_subscription_next_due_timestamp_seconds'), {})


if __name__ == '__main__':
    unittest.main()
