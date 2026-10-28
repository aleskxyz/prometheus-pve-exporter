"""
Tests for the Proxmox VE cluster collectors.
"""

import unittest

from proxmoxer import ResourceException

from fakes import FakeAPI, ids, samples, series

from pve_exporter.collector.cluster import (
    ClusterInfoCollector,
    ClusterNodeCollector,
    ClusterResourcesCollector,
    QDeviceCollector,
    StatusCollector,
    StorageConfigCollector,
    VersionCollector,
)

CLUSTER_STATUS = [
    {'type': 'node', 'id': 'node/pve1', 'name': 'pve1', 'online': 1},
    {'type': 'cluster', 'id': 'cluster', 'name': 'pvec', 'quorate': 1},
]

RESOURCES = [
    {'type': 'node', 'id': 'node/pve1', 'node': 'pve1', 'cpu': 0.1, 'maxcpu': 8,
     'mem': 100, 'maxmem': 1000, 'disk': 10, 'maxdisk': 100, 'uptime': 50,
     'hastate': 'online'},
    {'type': 'qemu', 'id': 'qemu/100', 'vmid': 100, 'node': 'pve1', 'name': 'vm100', 'template': 0,
     'pool': 'prod',
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



def fake_pve(status=None, resources=None, **routes):
    """A fake PVE with /cluster/status, /cluster/resources and further routes."""
    return FakeAPI(dict({'cluster/status': status or CLUSTER_STATUS,
                         'cluster/resources': resources or RESOURCES}, **routes))


class ClusterResourcesCollectorTest(unittest.TestCase):
    """Tests for ClusterResourcesCollector."""

    def test_metrics_per_kind_of_resource(self):
        result = samples(ClusterResourcesCollector(fake_pve()).collect())
        self.assertEqual(ids(result, 'pve_node_cpus'), {'node/pve1'})
        self.assertEqual(ids(result, 'pve_guest_cpus'), {'qemu/100', 'qemu/101', 'lxc/200'})
        self.assertEqual(ids(result, 'pve_storage_size_bytes'), {'storage/pve1/local'})
        self.assertEqual(result[('pve_node_rootfs_size_bytes', frozenset({('id', 'node/pve1')}))], 100)
        self.assertEqual(result[('pve_guest_disk_size_bytes', frozenset({('id', 'qemu/100')}))], 50)
        self.assertEqual(result[('pve_storage_usage_bytes', frozenset({('id', 'storage/pve1/local')}))], 10)
        self.assertEqual(result[('pve_storage_shared', frozenset({('id', 'storage/pve1/local')}))], 0)
        for old in ('pve_cpu_usage_ratio', 'pve_cpu_usage_limit', 'pve_disk_size_bytes', 'pve_memory_usage_bytes',
                    'pve_uptime_seconds', 'pve_ha_state', 'pve_lock_state'):
            self.assertNotIn(old, {name for name, _ in result})

    def test_pools_are_skipped(self):
        result = samples(ClusterResourcesCollector(fake_pve()).collect())
        for name in ('pve_guest_cpu_usage_ratio', 'pve_guest_memory_usage_bytes', 'pve_guest_uptime_seconds'):
            self.assertNotIn('/pool/prod', ids(result, name))
            self.assertIn('qemu/100', ids(result, name))

    def test_qemu_zero_disk_usage_is_skipped(self):
        result = samples(ClusterResourcesCollector(fake_pve()).collect())
        self.assertEqual(ids(result, 'pve_guest_disk_usage_bytes'), {'lxc/200'})

    def test_qemu_nonzero_disk_usage_is_kept(self):
        resources = [dict(RESOURCES[1], disk=42)]
        result = samples(ClusterResourcesCollector(fake_pve(resources=resources)).collect())
        self.assertEqual(result[('pve_guest_disk_usage_bytes', frozenset({('id', 'qemu/100')}))], 42)

    def test_guest_info_has_vmid_and_pool(self):
        result = samples(ClusterResourcesCollector(fake_pve()).collect())
        info = {dict(labels)['id']: dict(labels) for (name, labels) in result
                if name == 'pve_guest_info'}
        self.assertEqual(info['qemu/100']['vmid'], '100')
        self.assertEqual(info['qemu/100']['pool'], 'prod')
        self.assertEqual(info['lxc/200']['pool'], '')

    def test_traffic_as_counters(self):
        result = samples(ClusterResourcesCollector(fake_pve()).collect())
        self.assertEqual(result[('pve_guest_network_transmit_bytes_total', frozenset({('id', 'qemu/100')}))], 2)
        self.assertEqual(result[('pve_guest_disk_read_bytes_total', frozenset({('id', 'lxc/200')}))], 3)
        self.assertNotIn('node/pve1', ids(result, 'pve_guest_network_receive_bytes_total'))

    def test_dense_states(self):
        result = samples(ClusterResourcesCollector(fake_pve()).collect())
        node_ha = [key for key in result if key[0] == 'pve_node_ha_state']
        guest_ha = [key for key in result if key[0] == 'pve_guest_ha_state']
        lock_states = [key for key in result if key[0] == 'pve_guest_lock_state']
        # 1 node x 5 states, 3 guests x 11 states, 3 guests x 13 locks.
        self.assertEqual(len(node_ha), 5)
        self.assertEqual(len(guest_ha), 3 * 11)
        self.assertEqual(len(lock_states), 3 * 13)
        self.assertEqual(
            result[('pve_guest_lock_state', frozenset({('id', 'qemu/100'), ('state', 'backup')}))], 1)

    def test_sparse_states(self):
        result = samples(ClusterResourcesCollector(fake_pve(), sparse_states=True).collect())
        self.assertEqual(ids(result, 'pve_node_ha_state'), {'node/pve1'})
        self.assertEqual(ids(result, 'pve_guest_ha_state'), {'qemu/101'})
        self.assertEqual(
            result[('pve_guest_ha_state', frozenset({('id', 'qemu/101'), ('state', 'started')}))], 1)
        self.assertEqual(
            {key: value for key, value in result.items() if key[0] == 'pve_guest_lock_state'},
            {('pve_guest_lock_state', frozenset({('id', 'qemu/100'), ('state', 'backup')})): 1})


class StatusCollectorTest(unittest.TestCase):
    """One state metric per kind of resource."""

    def test_states(self):
        status = CLUSTER_STATUS + [{'type': 'qdevice', 'id': 'qdevice'}]
        pve = fake_pve(status=status)
        result = samples(StatusCollector(pve).collect())
        self.assertEqual(ids(result, 'pve_node_online'), {'node/pve1'})
        self.assertEqual(result[('pve_node_online', frozenset({('id', 'node/pve1')}))], 1)
        self.assertEqual(result[('pve_cluster_quorate', frozenset({('id', 'cluster/pvec')}))], 1)
        self.assertEqual(ids(result, 'pve_guest_running'), {'qemu/100', 'qemu/101', 'lxc/200'})
        self.assertEqual(result[('pve_storage_available', frozenset({('id', 'storage/pve1/local')}))], 1)
        self.assertNotIn('pve_up', {name for name, _ in result})


class ClusterInfoCollectorTest(unittest.TestCase):
    """The number of nodes is a value, not a label."""

    def test_info_and_nodes(self):
        status = [dict(CLUSTER_STATUS[0]), dict(CLUSTER_STATUS[1], nodes=3, version=7)]
        result = samples(ClusterInfoCollector(fake_pve(status=status)).collect())
        self.assertEqual(result[('pve_cluster_info', frozenset({('id', 'cluster/pvec'), ('version', '7')}))], 1)
        self.assertEqual(result[('pve_cluster_nodes', frozenset({('id', 'cluster/pvec')}))], 3)


class QDeviceCollectorTest(unittest.TestCase):
    """The connection state is the value of pve_qdevice_up only."""

    def test_state_not_in_info(self):
        pve = fake_pve(**{'cluster/config/qdevice': {
            'Model': 'Net', 'Algorithm': 'Fifty-Fifty split', 'QNetd host': '10.0.0.1:5403',
            'Tie-breaker': 'Node with lowest node ID', 'State': 'Connected'}})
        result = samples(QDeviceCollector(pve).collect())
        self.assertEqual(result[('pve_qdevice_up', frozenset({('id', 'cluster/pvec')}))], 1)
        info = [dict(labels) for name, labels in result if name == 'pve_qdevice_info']
        self.assertEqual(len(info), 1)
        self.assertNotIn('state', info[0])

    def test_no_qdevice(self):
        # PVE answers an empty object for a cluster without QDevice
        pve = fake_pve(**{'cluster/config/qdevice': {}})
        self.assertEqual(list(QDeviceCollector(pve).collect()), [])

    def test_pve_without_qdevice_api(self):
        pve = fake_pve(**{'cluster/config/qdevice': ResourceException(
            501, 'Not Implemented', "Method 'GET /cluster/config/qdevice' not implemented")})
        self.assertEqual(list(QDeviceCollector(pve).collect()), [])

    def test_error_fails_the_collector(self):
        """A missing permission is not taken for a cluster without QDevice."""
        pve = fake_pve(**{'cluster/config/qdevice': ResourceException(
            403, 'Forbidden', 'Permission check failed (/, Sys.Audit)')})
        with self.assertRaises(ResourceException):
            list(QDeviceCollector(pve).collect())

    def test_standalone_node(self):
        pve = fake_pve(status=CLUSTER_STATUS[:1])
        self.assertEqual(list(QDeviceCollector(pve).collect()), [])


class VersionCollectorTest(unittest.TestCase):
    """The build of PVE as labels."""

    def test_version(self):
        pve = fake_pve(version={'release': '9.0', 'repoid': 'abc', 'version': '9.0.3', 'x': 1})
        self.assertEqual(series(samples(VersionCollector(pve).collect()), 'pve_version_info'), {
            frozenset({('release', '9.0'), ('repoid', 'abc'), ('version', '9.0.3')}): 1})

    def test_no_version_keys(self):
        result = samples(VersionCollector(fake_pve(version={})).collect())
        self.assertEqual(result, {})


class ClusterNodeCollectorTest(unittest.TestCase):
    """One info series per node of /cluster/status."""

    def test_nodes(self):
        status = [dict(CLUSTER_STATUS[0], level='c', nodeid=1), CLUSTER_STATUS[1],
                  {'type': 'node', 'id': 'node/pve2', 'name': 'pve2', 'online': 0, 'nodeid': 2}]
        result = samples(ClusterNodeCollector(fake_pve(status=status)).collect())
        self.assertEqual(series(result, 'pve_node_info'), {
            frozenset({('id', 'node/pve1'), ('level', 'c'), ('name', 'pve1'), ('nodeid', '1')}): 1,
            # a missing key is an empty label
            frozenset({('id', 'node/pve2'), ('level', ''), ('name', 'pve2'), ('nodeid', '2')}): 1,
        })

    def test_no_nodes(self):
        families = list(ClusterNodeCollector(fake_pve(status=CLUSTER_STATUS[1:])).collect())
        self.assertEqual([(family.name, family.samples) for family in families], [('pve_node_info', [])])


class StorageConfigCollectorTest(unittest.TestCase):
    """pve_storage_config_info carries the PBS target of a storage."""

    def test_pbs_and_dir(self):
        api = FakeAPI({'storage': [
            {'storage': 'pbs1', 'type': 'pbs', 'server': '10.0.0.1', 'datastore': 'store1',
             'namespace': 'DC1', 'fingerprint': 'AA:BB', 'content': 'backup'},
            {'storage': 'local', 'type': 'dir', 'path': '/var/lib/vz', 'disable': 1},
        ]})
        result = series(samples(StorageConfigCollector(api).collect()), 'pve_storage_config_info')
        self.assertEqual(result, {
            frozenset({('storage', 'pbs1'), ('type', 'pbs'), ('server', '10.0.0.1'),
                       ('port', ''), ('datastore', 'store1'), ('datastore_namespace', 'DC1'),
                       ('fingerprint', 'aa:bb'), ('disabled', '0')}): 1,
            frozenset({('storage', 'local'), ('type', 'dir'), ('server', ''), ('port', ''),
                       ('datastore', ''), ('datastore_namespace', ''), ('fingerprint', ''),
                       ('disabled', '1')}): 1,
        })


if __name__ == '__main__':
    unittest.main()
