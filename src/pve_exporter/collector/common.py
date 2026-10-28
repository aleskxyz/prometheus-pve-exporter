"""
Helpers shared by the Proxmox VE collectors.
"""

import logging
import typing

from prometheus_client.core import GaugeMetricFamily

from pve_exporter.collector.scrape_api import TargetUnreachable
from pve_exporter.util import parse_bool

LOG = logging.getLogger('gunicorn.error')

GUEST_TYPES = ('qemu', 'lxc')


def label(value) -> str:
    """Format an API value as label value; missing is empty."""
    return '' if value is None else str(value)


def flag(value) -> str:
    """Format a PVE boolean as label value "0" or "1"."""
    return '1' if parse_bool(value) else '0'


def labels(resource: dict[str, typing.Any], keys: typing.Sequence[str],
           csv_keys: typing.Sequence[str] = ()) -> list[str]:
    """
    Label values of a resource. Labels with comma-separated values are
    randomly ordered by Proxmox, which would make a new series on every
    scrape: they are sorted.
    """
    values = [label(resource.get(key)) for key in keys]
    for key in csv_keys:
        values.append(','.join(sorted(label(resource.get(key)).split(','))))
    return values


def families(prefix: str, specs: dict[str, tuple[str, str]],
             cls=GaugeMetricFamily) -> dict:
    """Metric families labelled by id, per API key: {key: (name suffix, help)}."""
    return {key: cls(f'{prefix}_{name}', doc, labels=['id'])
            for key, (name, doc) in specs.items()}


def summarize(items: typing.Sequence, limit: int = 10) -> str:
    """Comma-separated items, the first limit of them only."""
    text = ', '.join(map(str, items[:limit]))
    if len(items) > limit:
        text += f' and {len(items) - limit} more'
    return text


class ReadFailures:
    """
    The items (guests, jobs, nodes) a collector could not read, summarized
    in one message instead of a log per item. TargetUnreachable is no item's
    failure and fails the collector.
    """

    def __init__(self, what: str):
        self._what = what
        self.items = []
        self._last_error = None

    def read(self, item, read: typing.Callable):
        """Return read(), or None after recording that item could not be read."""
        try:
            return read()
        except TargetUnreachable:
            raise
        except Exception as error:  # pylint: disable=broad-except
            LOG.debug('Reading %s %s failed', self._what, item, exc_info=True)
            self.items.append(item)
            self._last_error = error
            return None

    def summary(self) -> str | None:
        """E.g. "backup jobs a, b unreadable (last error: ...)", None without failures."""
        if not self.items:
            return None
        return f'{self._what} {summarize(self.items)} unreadable (last error: {self._last_error})'


class StateSetMetric(GaugeMetricFamily):
    """
    A gauge with one series per possible state of a resource, 1 for the
    state the resource is in and 0 for the others.
    """

    def __init__(self, name: str, documentation: str, states: typing.Sequence[str],
                 state_label: str = 'state'):
        super().__init__(name, documentation, labels=['id', state_label])
        self._states = states

    def add_states(self, id_: str, current, only_current: bool = False):
        """
        Add the states of resource id_. With only_current, only the current
        state is added, and nothing if it is not a known state.
        """
        if only_current:
            if current in self._states:
                self.add_metric([id_, current], 1)
            return
        for state in self._states:
            self.add_metric([id_, state], current == state)


def resources(pve, *types: str) -> list[dict]:
    """
    The /cluster/resources of the given types. They are filtered from the
    unfiltered list, so that every collector shares one request per scrape.
    """
    return [resource for resource in pve.cluster.resources.get()
            if resource.get('type') in types]


def local_node(pve) -> str:
    """The name of the node that answers the API (/cluster/status)."""
    for entry in pve.cluster.status.get():
        if entry['type'] == 'node' and entry.get('local'):
            return entry['name']
    raise LookupError('/cluster/status lists no local node')


def cluster_entry(pve) -> dict | None:
    """The entry of the cluster in /cluster/status, None for a standalone node."""
    return next((entry for entry in pve.cluster.status.get() if entry['type'] == 'cluster'), None)


def cluster_id(entry: dict) -> str:
    """The id of a cluster entry of /cluster/status: cluster/<name>."""
    return f"cluster/{entry['name']}"
