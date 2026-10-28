"""
Prometheus collectors for Proxmox VE, and the scrape that runs them.
"""

import collections
import logging
import time
import typing
from dataclasses import dataclass

from proxmoxer import ProxmoxAPI

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

from pve_exporter.collector.backup import BackupJobsCollector, BackupTasksCollector
from pve_exporter.collector.cluster import (
    StatusCollector,
    ClusterResourcesCollector,
    ClusterNodeCollector,
    VersionCollector,
    ClusterInfoCollector,
    QDeviceCollector,
    StorageConfigCollector,
)
from pve_exporter.collector.guest import GuestCreatedCollector
from pve_exporter.collector.node import (
    NodeConfigCollector,
    NodeReplicationCollector,
    SubscriptionCollector
)
from pve_exporter.collector.scrape_api import ScrapeAPI, TargetUnreachable
from pve_exporter.scrape_metrics import measure_scrape

LOG = logging.getLogger('gunicorn.error')

CLUSTER = 'cluster'
NODE = 'node'


@dataclass(frozen=True)
class CollectorSpec:
    """
    A collector the exporter can run: its name (the --collector.<name> flag
    with - for _, and the collector label of pve_collector_success), whether
    the cluster or node url parameter selects it, whether it is on by
    default, the help of its flag and the factory
    (pve, cache_scope, options) -> collector.
    """
    name: str
    scope: str
    default: bool
    help: str
    factory: typing.Callable

    @property
    def flag(self) -> str:
        """The command line flag that enables the collector."""
        return '--collector.' + self.name.replace('_', '-')


COLLECTORS = (
    CollectorSpec('status', CLUSTER, True, 'Exposes Node/VM/CT-Status',
                  lambda pve, scope, options: StatusCollector(pve)),
    CollectorSpec('resources', CLUSTER, True, 'Exposes PVE resources info',
                  lambda pve, scope, options: ClusterResourcesCollector(
                      pve, options.sparse_states)),
    CollectorSpec('node', CLUSTER, True, 'Exposes PVE node info',
                  lambda pve, scope, options: ClusterNodeCollector(pve)),
    CollectorSpec('cluster', CLUSTER, True, 'Exposes PVE cluster info',
                  lambda pve, scope, options: ClusterInfoCollector(pve)),
    CollectorSpec('version', CLUSTER, True, 'Exposes PVE version info',
                  lambda pve, scope, options: VersionCollector(pve)),
    CollectorSpec('qdevice', CLUSTER, True, 'Exposes PVE QDevice connection state',
                  lambda pve, scope, options: QDeviceCollector(pve)),
    CollectorSpec('storage_config', CLUSTER, True, 'Exposes the storage configuration',
                  lambda pve, scope, options: StorageConfigCollector(pve)),
    CollectorSpec('backup_jobs', CLUSTER, True,
                  'Exposes backup jobs, the guests they select and their scheduled runs',
                  lambda pve, scope, options: BackupJobsCollector(pve, scope)),
    CollectorSpec('guest_created', CLUSTER, True,
                  'Exposes the creation time of qemu guests (reads each guest config once)',
                  lambda pve, scope, options: GuestCreatedCollector(pve, scope)),
    CollectorSpec('backup_tasks', CLUSTER, False,
                  'Exposes the newest finished and the running vzdump tasks of each node',
                  lambda pve, scope, options: BackupTasksCollector(pve)),
    CollectorSpec('subscription', NODE, True, 'Exposes PVE subscription info',
                  lambda pve, scope, options: SubscriptionCollector(pve)),
    CollectorSpec('config', NODE, True, 'Exposes PVE onboot status',
                  lambda pve, scope, options: NodeConfigCollector(pve)),
    CollectorSpec('replication', NODE, True, 'Exposes PVE replication info',
                  lambda pve, scope, options: NodeReplicationCollector(pve)),
)

# Whether each collector is enabled, and options of the collectors.
CollectorsOptions = collections.namedtuple(
    'CollectorsOptions',
    [spec.name for spec in COLLECTORS] + ['sparse_states'],
    defaults=[spec.default for spec in COLLECTORS] + [False])


class GuardedCollector:  # pylint: disable=too-few-public-methods
    """
    Runs a collector so that its failure does not fail the scrape. An
    exception drops the collector's metrics; a partial_failure the collector
    reports keeps the metrics it could collect. Both are recorded as failure
    of this collector only.
    """

    def __init__(self, name, collector, results):
        self._name = name
        self._collector = collector
        self._results = results

    def collect(self):  # pylint: disable=missing-docstring
        start = time.monotonic()
        try:
            families = list(self._collector.collect())
            failure = getattr(self._collector, 'partial_failure', None)
            if failure:
                LOG.warning('Collector %s is incomplete: %s', self._name, failure)
        except TargetUnreachable as error:
            LOG.warning('Collector %s skipped: %s', self._name, error)
            families, failure = [], True
        except Exception:  # pylint: disable=broad-except
            LOG.exception('Collector %s failed', self._name)
            families, failure = [], True
        self._results[self._name] = (not failure, time.monotonic() - start)
        return families


class CollectorStatusCollector:  # pylint: disable=too-few-public-methods
    """
    Exposes the outcome of the guarded collectors of a scrape. Registered
    last, so that the others have run.
    """

    def __init__(self, results):
        self._results = results

    def collect(self):  # pylint: disable=missing-docstring
        success = GaugeMetricFamily(
            'pve_collector_success', 'Whether a collector succeeded in this scrape.',
            labels=['collector'])
        duration = GaugeMetricFamily(
            'pve_collector_duration_seconds', 'Duration of a collector in this scrape.',
            labels=['collector'])
        for name, (ok, seconds) in self._results.items():
            success.add_metric([name], ok)
            duration.add_metric([name], seconds)
        yield success
        yield duration


def collect_pve(config, host, cluster, node, options: CollectorsOptions, *,
                module='default', api=None):
    # pylint: disable=too-many-arguments
    """
    Scrape a host and return prometheus text format for it; api replaces the
    PVE client. Caches that outlive the scrape are kept per module and host,
    as the credentials of modules may see different guests and jobs. A target
    that does not answer the version call fails the scrape, instead of every
    collector.
    """

    with measure_scrape(host, cluster, node):
        pve = ScrapeAPI(api or ProxmoxAPI(host, **config), host)
        pve.version.get()

        scopes = {CLUSTER: cluster, NODE: node}
        registry = CollectorRegistry()
        results = {}
        for spec in COLLECTORS:
            if scopes[spec.scope] and getattr(options, spec.name):
                collector = spec.factory(pve, (module, host), options)
                registry.register(GuardedCollector(spec.name, collector, results))
        registry.register(CollectorStatusCollector(results))

        return generate_latest(registry)
