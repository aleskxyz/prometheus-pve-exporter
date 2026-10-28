"""
Prometheus collectors for Proxmox VE cluster wide state and configuration.
"""
# pylint: disable=too-few-public-methods

import itertools
import logging
import typing

from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily
from proxmoxer import ResourceException

from pve_exporter.collector.common import (
    GUEST_TYPES,
    StateSetMetric,
    cluster_entry,
    cluster_id,
    families,
    flag,
    label,
    labels,
    resources,
)

LOG = logging.getLogger('gunicorn.error')


class StatusCollector:
    """
    Collects the state of nodes, the cluster, guests and storages. E.g.:

    # HELP pve_node_online Whether the node is online
    pve_node_online{id="node/proxmox-host"} 1.0
    pve_cluster_quorate{id="cluster/pvec"} 1.0
    pve_guest_running{id="qemu/102"} 1.0
    pve_storage_available{id="storage/proxmox-host/local"} 1.0
    """

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        node_online = GaugeMetricFamily(
            'pve_node_online', 'Whether the node is online (/cluster/status).', labels=['id'])
        cluster_quorate = GaugeMetricFamily(
            'pve_cluster_quorate', 'Whether the cluster is quorate (/cluster/status).',
            labels=['id'])
        guest_running = GaugeMetricFamily(
            'pve_guest_running', 'Whether the guest is running (status of /cluster/resources).',
            labels=['id'])
        storage_available = GaugeMetricFamily(
            'pve_storage_available',
            'Whether the storage is available on the node (status of /cluster/resources).',
            labels=['id'])

        for entry in self._pve.cluster.status.get():
            if entry['type'] == 'node':
                node_online.add_metric([entry['id']], entry['online'])
            elif entry['type'] == 'cluster':
                cluster_quorate.add_metric([cluster_id(entry)], entry['quorate'])
            else:
                LOG.debug('Skipping cluster status entry of type %s', entry['type'])

        for resource in resources(self._pve, *GUEST_TYPES):
            guest_running.add_metric([resource['id']], resource.get('status') == 'running')

        for resource in resources(self._pve, 'storage'):
            storage_available.add_metric([resource['id']], resource.get('status') == 'available')

        yield from (node_online, cluster_quorate, guest_running, storage_available)


class VersionCollector:
    """
    Collects Proxmox VE build information. E.g.:

    # HELP pve_version_info Proxmox VE version info
    # TYPE pve_version_info gauge
    pve_version_info{release="15",repoid="7599e35a",version="4.4"} 1.0
    """

    LABELS = ['release', 'repoid', 'version']

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        version = self._pve.version.get()
        metric = GaugeMetricFamily(
            'pve_version_info',
            'Proxmox VE version info',
            labels=self.LABELS
        )
        if any(key in version for key in self.LABELS):
            metric.add_metric(labels(version, self.LABELS), 1)

        yield metric


class ClusterNodeCollector:
    """
    Collects Proxmox VE cluster node information. E.g.:

    # HELP pve_node_info Node info
    # TYPE pve_node_info gauge
    pve_node_info{id="node/proxmox-host", level="c", name="proxmox-host",
        nodeid="0"} 1.0
    """

    LABELS = ['id', 'level', 'name', 'nodeid']

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        info_metrics = GaugeMetricFamily('pve_node_info', 'Node info', labels=self.LABELS)
        for entry in self._pve.cluster.status.get():
            if entry['type'] == 'node':
                info_metrics.add_metric(labels(entry, self.LABELS), 1)
        yield info_metrics


class ClusterInfoCollector:
    """
    Collects Proxmox VE cluster information. E.g.:

    # HELP pve_cluster_info Cluster info
    # TYPE pve_cluster_info gauge
    pve_cluster_info{id="cluster/pvec",version="2"} 1.0
    pve_cluster_nodes{id="cluster/pvec"} 2.0
    """

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        info = GaugeMetricFamily(
            'pve_cluster_info', 'Cluster info (version of the cluster configuration), always 1.',
            labels=['id', 'version'])
        nodes = GaugeMetricFamily(
            'pve_cluster_nodes', 'Number of nodes of the cluster.', labels=['id'])
        entry = cluster_entry(self._pve)
        if entry is not None:
            info.add_metric([cluster_id(entry), label(entry.get('version'))], 1)
            if entry.get('nodes') is not None:
                nodes.add_metric([cluster_id(entry)], entry['nodes'])
        yield info
        yield nodes


class QDeviceCollector:
    """
    Collects Proxmox VE QDevice connection state from the local node's view.
    For manual test: "pvesh get /cluster/config/qdevice"

    # HELP pve_qdevice_up Proxmox VE QDevice is connected (1) or not (0)
    # TYPE pve_qdevice_up gauge
    pve_qdevice_up{id="cluster/pvec"} 1.0
    # HELP pve_qdevice_info Proxmox VE QDevice info (1 if configured)
    # TYPE pve_qdevice_info gauge
    pve_qdevice_info{id="cluster/pvec",model="Net",algorithm="Fifty-Fifty split",
        qnetd_host="10.0.0.1:5403",tie_breaker="Node with lowest node ID"} 1.0
    """

    # Maps API response keys to Prometheus label names; the state is the value
    # of pve_qdevice_up.
    LABEL_MAP = {
        'Model': 'model',
        'Algorithm': 'algorithm',
        'QNetd host': 'qnetd_host',
        'Tie-breaker': 'tie_breaker',
    }

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        entry = cluster_entry(self._pve)
        if entry is None:
            return
        id_ = cluster_id(entry)

        try:
            qdevice = self._pve.cluster.config.qdevice.get()
        except ResourceException as error:
            # PVE versions without the QDevice API; other errors, e.g. missing
            # permissions, fail the collector.
            if error.status_code == 501:
                return
            raise

        # Without a QDevice, PVE answers an empty object.
        if not qdevice:
            return

        info_metric = GaugeMetricFamily(
            'pve_qdevice_info',
            'Proxmox VE QDevice info (1 if configured)',
            labels=['id'] + list(self.LABEL_MAP.values()))
        info_metric.add_metric([id_] + labels(qdevice, list(self.LABEL_MAP)), 1)

        up_metric = GaugeMetricFamily(
            'pve_qdevice_up',
            'Proxmox VE QDevice is connected (1) or not (0)',
            labels=['id'])
        up_metric.add_metric([id_], qdevice.get('State') == 'Connected')

        yield up_metric
        yield info_metric


class StorageConfigCollector:
    """
    Collects the storage configuration (cluster wide, from /storage). E.g.:

    # HELP pve_storage_config_info Storage configuration
    # TYPE pve_storage_config_info gauge
    pve_storage_config_info{storage="pbs1",type="pbs",server="10.0.0.1",port="",
        datastore="store1",datastore_namespace="dc1",fingerprint="aa:bb",disabled="0"} 1.0
    """

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        info = GaugeMetricFamily(
            'pve_storage_config_info',
            ('Storage configuration, always 1. Keys a storage type does not have are empty; '
             'fingerprint is lower case.'),
            labels=['storage', 'type', 'server', 'port', 'datastore', 'datastore_namespace',
                    'fingerprint', 'disabled'])
        for storage in self._pve.storage.get():
            info.add_metric(
                labels(storage, ['storage', 'type', 'server', 'port', 'datastore', 'namespace'])
                + [label(storage.get('fingerprint')).strip().lower(), flag(storage.get('disable'))],
                1)
        yield info


HA_GUEST_STATES = [
    'stopped',
    'request_stop',
    'request_start',
    'request_start_balance',
    'started',
    'fence',
    'recovery',
    'migrate',
    'relocate',
    'freeze',
    'error',
]

HA_NODE_STATES = ['online', 'maintenance', 'unknown', 'fence', 'gone']

# qemu and LXC locks
LOCK_STATES = [
    'backup',
    'clone',
    'create',
    'destroyed',
    'disk',
    'fstrim',
    'migrate',
    'mounted',
    'rollback',
    'snapshot',
    'snapshot-delete',
    'suspended',
    'suspending',
]


class ClusterResourcesCollector:
    """
    Collects the usage of nodes, guests and storages from /cluster/resources,
    one set of metrics per kind of resource.

    If sparse_states is set, pve_guest_ha_state is only exposed for HA managed
    guests and pve_guest_lock_state only for the lock a guest currently holds.
    """

    def __init__(self, pve, sparse_states: bool = False):
        self._pve = pve
        self._sparse_states = sparse_states

    def collect(self):  # pylint: disable=missing-docstring
        node_metrics = families('pve_node', {
            'cpu': ('cpu_usage_ratio', 'CPU utilization of the node.'),
            'maxcpu': ('cpus', 'Number of CPUs of the node.'),
            'mem': ('memory_usage_bytes', 'Used memory of the node.'),
            'maxmem': ('memory_size_bytes', 'Memory of the node.'),
            'disk': ('rootfs_usage_bytes', 'Used space of the root file system of the node.'),
            'maxdisk': ('rootfs_size_bytes', 'Size of the root file system of the node.'),
            'uptime': ('uptime_seconds', 'Uptime of the node.'),
        })
        guest_metrics = families('pve_guest', {
            'cpu': ('cpu_usage_ratio', 'CPU utilization of the guest.'),
            'maxcpu': ('cpus', 'Number of virtual CPUs of the guest.'),
            'mem': ('memory_usage_bytes', 'Used memory of the guest.'),
            'maxmem': ('memory_size_bytes', 'Memory of the guest.'),
            'disk': ('disk_usage_bytes',
                     'Used space of the root file system of a container; not exposed for VMs, '
                     'as PVE can not see inside them and reports 0.'),
            'maxdisk': ('disk_size_bytes', 'Size of the root disk of the guest.'),
            'uptime': ('uptime_seconds', 'Uptime of the guest.'),
        })
        guest_counters = families('pve_guest', {
            'netin': ('network_receive_bytes',
                      'Bytes the guest received over the network since it was started.'),
            'netout': ('network_transmit_bytes',
                       'Bytes the guest sent over the network since it was started.'),
            'diskread': ('disk_read_bytes',
                         'Bytes the guest read from its block devices since it was started '
                         '(not for all storage types).'),
            'diskwrite': ('disk_written_bytes',
                          'Bytes the guest wrote to its block devices since it was started '
                          '(not for all storage types).'),
        }, cls=CounterMetricFamily)
        storage_metrics = families('pve_storage', {
            'disk': ('usage_bytes', 'Used space of the storage on the node.'),
            'maxdisk': ('size_bytes', 'Size of the storage on the node.'),
            'shared': ('shared', 'Whether the storage is shared among cluster nodes.'),
        })

        node_ha = StateSetMetric(
            'pve_node_ha_state', 'HA state of the node.', HA_NODE_STATES)
        guest_ha = StateSetMetric(
            'pve_guest_ha_state', 'HA state of the guest (for HA managed guests).',
            HA_GUEST_STATES)
        lock_metric = StateSetMetric(
            'pve_guest_lock_state', "The guest's current config lock", LOCK_STATES)

        guest_labels = ['id', 'vmid', 'node', 'name', 'type', 'template', 'tags', 'pool']
        guest_info = GaugeMetricFamily('pve_guest_info', 'VM/CT info', labels=guest_labels)
        storage_info = GaugeMetricFamily(
            'pve_storage_info', 'Storage info',
            labels=['id', 'node', 'storage', 'plugintype', 'content'])

        # Pools (PVE 9+) report the sum of their members and other resource
        # types have no metrics: both are skipped.
        for resource in self._pve.cluster.resources.get():
            restype = resource['type']
            if restype == 'node':
                node_ha.add_states(resource['id'], resource.get('hastate'))
                self._add_values(node_metrics, resource)
            elif restype in GUEST_TYPES:
                guest_info.add_metric(labels(resource, guest_labels), 1)
                # In sparse mode, guests not managed by HA have no HA state.
                if not self._sparse_states or resource.get('hastate'):
                    guest_ha.add_states(resource['id'], resource.get('hastate'))
                lock_metric.add_states(resource['id'], resource.get('lock'),
                                       only_current=self._sparse_states)
                # PVE cannot see inside qemu guests and reports a disk usage of 0.
                skip = {'disk'} if restype == 'qemu' and not resource.get('disk') else set()
                self._add_values(guest_metrics, resource, skip)
                self._add_values(guest_counters, resource)
            elif restype == 'storage':
                storage_info.add_metric(
                    labels(resource, ['id', 'node', 'storage', 'plugintype'], ['content']), 1)
                self._add_values(storage_metrics, resource)

        return itertools.chain(
            node_metrics.values(), guest_metrics.values(), guest_counters.values(),
            storage_metrics.values(), [node_ha, guest_ha, lock_metric, guest_info, storage_info])

    @staticmethod
    def _add_values(metric_families: dict, resource: dict, skip: typing.Container = ()):
        for key, family in metric_families.items():
            if key in resource and key not in skip:
                family.add_metric([resource['id']], resource[key])
