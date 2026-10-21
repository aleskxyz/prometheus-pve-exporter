"""
Tests for the Proxmox VE cluster collectors.
"""

import unittest

from pve_exporter.collector.cluster import BackupInfoCollector, ClusterResourcesCollector


CLUSTER_STATUS = [
    {'type': 'node', 'id': 'node/pve1', 'name': 'pve1', 'online': 1},
    {'type': 'cluster', 'id': 'cluster', 'name': 'pvec', 'quorate': 1},
]

STANDALONE_STATUS = [
    {'type': 'node', 'id': 'node/pve1', 'name': 'pve1', 'online': 1},
]

RESOURCES = [
    {'type': 'node', 'id': 'node/pve1', 'node': 'pve1', 'cpu': 0.1, 'maxcpu': 8,
     'mem': 100, 'maxmem': 1000, 'disk': 10, 'maxdisk': 100, 'uptime': 50,
     'hastate': 'online'},
    {'type': 'qemu', 'id': 'qemu/100', 'node': 'pve1', 'name': 'vm100', 'template': 0,
     'cpu': 0.5, 'maxcpu': 2, 'mem': 10, 'maxmem': 20, 'disk': 0, 'maxdisk': 50,
     'uptime': 5, 'netin': 1, 'netout': 2, 'diskread': 3, 'diskwrite': 4,
     'lock': 'backup'},
    {'type': 'qemu', 'id': 'qemu/101', 'node': 'pve1', 'name': 'vm101', 'template': 0,
     'cpu': 0.2, 'maxcpu': 2, 'mem': 10, 'maxmem': 20, 'disk': 0, 'maxdisk': 50,
     'uptime': 5, 'netin': 1, 'netout': 2, 'diskread': 3, 'diskwrite': 4,
     'hastate': 'started'},
    {'type': 'lxc', 'id': 'lxc/200', 'node': 'pve1', 'name': 'ct200', 'template': 0,
     'cpu': 0.1, 'maxcpu': 1, 'mem': 5, 'maxmem': 10, 'disk': 7, 'maxdisk': 30,
     'uptime': 5, 'netin': 1, 'netout': 2, 'diskread': 3, 'diskwrite': 4},
    {'type': 'storage', 'id': 'storage/pve1/local', 'node': 'pve1', 'storage': 'local',
     'plugintype': 'dir', 'content': 'iso,backup', 'disk': 10, 'maxdisk': 100,
     'shared': 0, 'status': 'available'},
    {'type': 'pool', 'id': '/pool/prod', 'pool': 'prod', 'cpu': 0.7, 'maxcpu': 4,
     'mem': 20, 'maxmem': 40, 'uptime': 5},
]


class _Endpoint:
    def __init__(self, data):
        self._data = data

    def get(self, **_kwargs):  # pylint: disable=missing-function-docstring
        return self._data


class _Cluster:
    def __init__(self, status, resources, not_backed_up):
        self.status = _Endpoint(status)
        self.resources = _Endpoint(resources)
        self._not_backed_up = not_backed_up

    def __call__(self, path):
        assert path == 'backup-info/not-backed-up'
        return _Endpoint(self._not_backed_up)


class FakePVE:  # pylint: disable=too-few-public-methods
    """Minimal stand-in for proxmoxer.ProxmoxAPI."""

    def __init__(self, status=None, resources=None, not_backed_up=None):
        self.cluster = _Cluster(status or CLUSTER_STATUS, resources or RESOURCES,
                                not_backed_up or [])


def samples(families):
    """Flatten metric families into {(name, frozenset(labels)): value}."""
    return {
        (sample.name, frozenset(sample.labels.items())): sample.value
        for family in families
        for sample in family.samples
    }


def ids(result, name):
    """Return the set of id label values exposed for a metric."""
    return {dict(labels)['id'] for (metric, labels) in result if metric == name}


class ClusterResourcesCollectorTest(unittest.TestCase):
    """Tests for ClusterResourcesCollector."""

    def test_pools_are_skipped(self):
        result = samples(ClusterResourcesCollector(FakePVE()).collect())
        for name in ('pve_cpu_usage_ratio', 'pve_memory_usage_bytes', 'pve_uptime_seconds'):
            self.assertNotIn('/pool/prod', ids(result, name))
            self.assertIn('qemu/100', ids(result, name))

    def test_qemu_zero_disk_usage_is_skipped(self):
        result = samples(ClusterResourcesCollector(FakePVE()).collect())
        self.assertEqual(ids(result, 'pve_disk_usage_bytes'),
                         {'node/pve1', 'lxc/200', 'storage/pve1/local'})
        self.assertIn('qemu/100', ids(result, 'pve_disk_size_bytes'))

    def test_qemu_nonzero_disk_usage_is_kept(self):
        resources = [dict(RESOURCES[1], disk=42)]
        result = samples(ClusterResourcesCollector(FakePVE(resources=resources)).collect())
        self.assertEqual(result[('pve_disk_usage_bytes', frozenset({('id', 'qemu/100')}))], 42)

    def test_dense_states(self):
        result = samples(ClusterResourcesCollector(FakePVE()).collect())
        ha_states = [key for key in result if key[0] == 'pve_ha_state']
        lock_states = [key for key in result if key[0] == 'pve_lock_state']
        # 1 node x 5 states + 3 guests x 11 states, 3 guests x 9 states.
        self.assertEqual(len(ha_states), 5 + 3 * 11)
        self.assertEqual(len(lock_states), 3 * 9)
        self.assertEqual(
            result[('pve_lock_state', frozenset({('id', 'qemu/100'), ('state', 'backup')}))], 1)

    def test_sparse_states(self):
        result = samples(ClusterResourcesCollector(FakePVE(), sparse_states=True).collect())
        self.assertEqual(ids(result, 'pve_ha_state'), {'node/pve1', 'qemu/101'})
        self.assertEqual(
            result[('pve_ha_state', frozenset({('id', 'qemu/101'), ('state', 'started')}))], 1)
        self.assertEqual(
            {key: value for key, value in result.items() if key[0] == 'pve_lock_state'},
            {('pve_lock_state', frozenset({('id', 'qemu/100'), ('state', 'backup')})): 1})


class BackupInfoCollectorTest(unittest.TestCase):
    """Tests for BackupInfoCollector."""

    def test_total_uses_cluster_entry(self):
        # CLUSTER_STATUS lists the node before the cluster entry.
        pve = FakePVE(not_backed_up=[{'type': 'qemu', 'vmid': 100}])
        result = samples(BackupInfoCollector(pve).collect())
        self.assertEqual(
            result[('pve_not_backed_up_total', frozenset({('id', 'cluster/pvec')}))], 1)
        self.assertEqual(
            result[('pve_not_backed_up_info', frozenset({('id', 'qemu/100')}))], 1)

    def test_standalone_node_has_no_total(self):
        pve = FakePVE(status=STANDALONE_STATUS, not_backed_up=[{'type': 'qemu', 'vmid': 100}])
        result = samples(BackupInfoCollector(pve).collect())
        self.assertNotIn('pve_not_backed_up_total', {name for name, _ in result})
        self.assertIn(('pve_not_backed_up_info', frozenset({('id', 'qemu/100')})), result)


if __name__ == '__main__':
    unittest.main()
