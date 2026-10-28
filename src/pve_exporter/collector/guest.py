"""
Prometheus collector for the creation time of Proxmox VE guests.
"""
# pylint: disable=too-few-public-methods

import itertools
import re
import threading
import time
import typing
from dataclasses import dataclass

from prometheus_client.core import GaugeMetricFamily

from pve_exporter.collector.common import ReadFailures, label, resources
from pve_exporter.util import IDLE_SECONDS, IdleExpiry

# Time one scrape may spend reading guest configs for their creation time; the
# rest follows in the next scrapes.
CREATED_BUDGET_SECONDS = 5.0
_META_CTIME = re.compile(r'(?:^|,)ctime=(\d+)')


@dataclass(frozen=True)
class Guest:
    """
    A qemu guest of /cluster/resources. Its config is readable unless its node
    is unknown or offline (status "unknown", which also lacks the name).
    """
    node: str
    name: str
    readable: bool


@dataclass
class CreatedTimes:
    """The creation times get() found, and what it could not read."""
    created: dict[int, int]
    failed: ReadFailures
    pending: int


class CreatedCache:
    """
    Creation time of qemu guests per scope (module and target), read once per
    guest. A guest is the VMID together with its name, so a reused VMID is
    read again; a guest whose config could not be read is tried again in the
    next scrapes, after the guests not tried yet and those that failed less
    recently, so that every guest gets its turn. Scopes are separate, as their
    credentials may see different guests; a scope not used for idle_seconds
    is forgotten.
    """

    def __init__(self, idle_seconds: float = IDLE_SECONDS):
        self._lock = threading.Lock()
        # {scope: {vmid: (name, ctime or None)}}
        self._created = {}
        # {scope: set of the VMIDs of the newest get()}
        self._present = {}
        # {scope: {vmid: when its config could not be read, in self._attempts}}
        self._failed = {}
        self._attempts = itertools.count()
        self._expiry = IdleExpiry(idle_seconds)

    def get(self, pve, scope: typing.Hashable, guests: dict[int, Guest]) -> CreatedTimes:
        """Return the creation time of the given qemu guests ({vmid: Guest})."""
        missing = self._missing(scope, guests)
        failures = ReadFailures('configs of qemu guests')
        pending = self._read(pve, scope, guests, missing, failures)
        with self._lock:
            for vmid in failures.items:
                if vmid in self._present[scope]:
                    self._failed[scope][vmid] = next(self._attempts)
            created = {vmid: ctime for vmid, (_, ctime) in self._created[scope].items()
                       if ctime is not None and vmid in guests}
        return CreatedTimes(created=created, failed=failures, pending=pending)

    def _missing(self, scope: typing.Hashable, guests: dict[int, Guest]) -> list[int]:
        """
        Forget idle scopes and the guests that went or changed; return the
        readable guests without creation time: those not tried yet first, then
        those that failed least recently, so that a guest whose read keeps
        failing (e.g. its node does not answer) does not use up the budget of
        the others.
        """
        with self._lock:
            for idle in self._expiry.use(scope, time.monotonic()):
                for state in (self._created, self._present, self._failed):
                    state.pop(idle, None)
            known = self._created.setdefault(scope, {})
            self._present[scope] = set(guests)
            for vmid in [vmid for vmid, (name, _) in known.items() if vmid not in guests
                         or (guests[vmid].readable and guests[vmid].name != name)]:
                del known[vmid]
            failed = self._failed.setdefault(scope, {})
            for vmid in [vmid for vmid in failed if vmid not in guests]:
                del failed[vmid]
            return sorted(
                (vmid for vmid in guests if vmid not in known and guests[vmid].readable),
                key=lambda vmid: (vmid in failed, failed.get(vmid, 0), vmid))

    def _read(self, pve, scope: typing.Hashable, guests: dict[int, Guest],
              missing: list[int], failures: ReadFailures) -> int:
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Read the configs of missing within the budget; return how many are left."""
        deadline = time.monotonic() + CREATED_BUDGET_SECONDS
        for index, vmid in enumerate(missing):
            if time.monotonic() > deadline:
                return len(missing) - index
            config = failures.read(
                vmid, lambda vmid=vmid: pve.nodes(guests[vmid].node).qemu(vmid).config.get())
            if config is None:
                continue
            match = _META_CTIME.search(str(config.get('meta', '')))
            with self._lock:
                # a concurrent scrape may have seen the guest go
                if vmid in self._present[scope]:
                    self._created[scope][vmid] = (
                        guests[vmid].name, int(match.group(1)) if match else None)
                self._failed[scope].pop(vmid, None)
        return 0


CREATED = CreatedCache()


class GuestCreatedCollector:
    """
    Collects the creation time of every qemu guest, from the ctime of its meta
    config option (LXC has none). A guest's config is read once and cached per
    scope; a VMID that disappears or gets another name is read again. Guests
    of unknown or offline nodes keep their cached creation time and are read
    once their node is back. A scrape starts no read after
    CREATED_BUDGET_SECONDS and leaves the rest to the next scrapes. E.g.:

    pve_guest_created_timestamp_seconds{id="qemu/100"} 1.7e9
    """

    def __init__(self, pve, scope: typing.Hashable, cache: CreatedCache | None = None):
        self._pve = pve
        self._scope = scope
        self._cache = CREATED if cache is None else cache
        self.partial_failure = None

    def collect(self):  # pylint: disable=missing-docstring
        created = GaugeMetricFamily(
            'pve_guest_created_timestamp_seconds',
            'Creation time of a qemu guest (ctime of its meta config option).',
            labels=['id'])
        guests = {
            int(resource['vmid']): Guest(
                node=label(resource.get('node')), name=label(resource.get('name')),
                readable=bool(resource.get('node')) and resource.get('status') != 'unknown')
            for resource in resources(self._pve, 'qemu')
            if resource.get('vmid') is not None
        }
        times = self._cache.get(self._pve, self._scope, guests)
        for vmid, ctime in sorted(times.created.items()):
            created.add_metric([f'qemu/{vmid}'], ctime)
        problems = [times.failed.summary()]
        if times.pending:
            problems.append(f'{times.pending} qemu guests not read yet (read time budget used up)')
        self.partial_failure = '; '.join(filter(None, problems)) or None
        yield created
