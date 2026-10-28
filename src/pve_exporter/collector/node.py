"""
Prometheus collectors for the node that answers the Proxmox VE API.
"""
# pylint: disable=too-few-public-methods

import itertools
from datetime import datetime, timezone

from prometheus_client.core import GaugeMetricFamily

from pve_exporter.collector.common import GUEST_TYPES, StateSetMetric, label, local_node
from pve_exporter.util import parse_bool


class NodeConfigCollector:
    """
    Collects Proxmox VE VM information directly from config, i.e. boot, name, onboot, etc.
    For manual test: "pvesh get /nodes/<node>/<type>/<vmid>/config"

    # HELP pve_guest_onboot Whether the guest starts at boot (onboot config option)
    # TYPE pve_guest_onboot gauge
    pve_guest_onboot{id="qemu/113",node="XXXX"} 1.0
    """

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        onboot = GaugeMetricFamily(
            'pve_guest_onboot',
            'Whether the guest starts at boot (onboot config option, 0 if not set).',
            labels=['id', 'node'])

        node = local_node(self._pve)
        for vmtype in GUEST_TYPES:
            guests = getattr(self._pve.nodes(node), vmtype)
            for vmdata in guests.get():
                config = guests(vmdata['vmid']).config.get()
                onboot.add_metric([f"{vmtype}/{vmdata['vmid']}", node],
                                  parse_bool(config.get('onboot')))

        yield onboot


class NodeReplicationCollector:
    """
    Collects Proxmox VE Replication information directly from status, i.e. replication duration,
    last_sync, last_try, next_sync, fail_count.
    For manual test: "pvesh get /nodes/<node>/replication/<id>/status"
    """

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        info = GaugeMetricFamily(
            'pve_replication_info',
            'Proxmox vm replication info',
            labels=['id', 'type', 'source', 'target', 'guest'])

        metrics = {
            'duration': GaugeMetricFamily(
                'pve_replication_duration_seconds',
                'Proxmox vm replication duration',
                labels=['id']),
            'last_sync': GaugeMetricFamily(
                'pve_replication_last_sync_timestamp_seconds',
                'Proxmox vm replication last_sync',
                labels=['id']),
            'last_try': GaugeMetricFamily(
                'pve_replication_last_try_timestamp_seconds',
                'Proxmox vm replication last_try',
                labels=['id']),
            'next_sync': GaugeMetricFamily(
                'pve_replication_next_sync_timestamp_seconds',
                'Proxmox vm replication next_sync',
                labels=['id']),
            'fail_count': GaugeMetricFamily(
                'pve_replication_failed_syncs',
                'Proxmox vm replication fail_count',
                labels=['id']),
        }

        node = local_node(self._pve)
        for jobdata in self._pve.nodes(node).replication.get():
            job_id = label(jobdata['id'])
            info.add_metric([
                job_id,
                label(jobdata['type']),
                f"node/{jobdata['source']}",
                f"node/{jobdata['target']}",
                f"{jobdata['vmtype']}/{jobdata['guest']}",
            ], 1)
            status = self._pve.nodes(node).replication(jobdata['id']).status.get()
            for key, metric_value in status.items():
                if key in metrics:
                    metrics[key].add_metric([job_id], metric_value)

        return itertools.chain(metrics.values(), [info])


class SubscriptionCollector:
    """
    Collects Proxmox VE subscription information (node, subscription level, status, next due date).
    """

    STATUSES = ["new", "notfound", "active", "invalid", "expired", "suspended"]

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        info_metric = GaugeMetricFamily(
            "pve_subscription_info",
            "Proxmox VE subscription info (1 if present)",
            labels=["id", "level"],
        )
        status_metric = StateSetMetric(
            "pve_subscription_status",
            "Proxmox VE subscription status (1 if matches status)",
            self.STATUSES,
            state_label="status",
        )
        next_due_metric = GaugeMetricFamily(
            "pve_subscription_next_due_timestamp_seconds",
            "Subscription next due date as Unix timestamp (midnight UTC)",
            labels=["id"],
        )

        node = local_node(self._pve)
        node_id = f"node/{node}"
        subscription = self._pve.nodes(node).subscription.get()

        info_metric.add_metric([node_id, subscription.get("level", "unknown")], 1)
        status_metric.add_states(node_id, subscription.get("status", "unknown"))

        next_due_date = subscription.get("nextduedate")
        if next_due_date:
            due = datetime.strptime(next_due_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            next_due_metric.add_metric([node_id], due.timestamp())

        yield info_metric
        yield status_metric
        yield next_due_metric
