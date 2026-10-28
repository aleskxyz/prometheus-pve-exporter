"""
Metrics concerning the operation of the Prometheus PVE exporter itself.
"""

import contextlib
import threading
import time

from prometheus_client.metrics import Counter, Histogram, Summary

from pve_exporter.util import IDLE_SECONDS, IdleExpiry

API_METRICS_ENABLED: bool = False
"""
Whether or not api metrics are enabled
"""

TARGET_METRICS_ENABLED: bool = False
"""
Whether or not target metrics are enabled
"""

API_DURATION = Summary(
    'pve_scrape_api_duration_seconds',
    'Duration of PVE API requests, per endpoint (path with the ids as {node}, {vmid}, {id}).',
    ['target', 'endpoint']
)
API_ERRORS = Counter(
    'pve_scrape_api_errors_total',
    'PVE API requests that failed, per endpoint.',
    ['target', 'endpoint']
)

TARGET_DURATION = Histogram(
    'pve_scrape_target_duration_seconds',
    'Duration of a scrape',
    ['target', 'cluster', 'node'],
    buckets=[val*10 for val in Histogram.DEFAULT_BUCKETS]
)
TARGET_ERRORS = Counter(
    'pve_scrape_target_errors_total',
    'Number of errors occured when scraping a target',
    ['target', 'cluster', 'node'],
)


_LOCK = threading.Lock()
_EXPIRY = IdleExpiry(IDLE_SECONDS)
# {target: {(metric, label values)}}: the series of each target
_SERIES = {}


def _use(target: str, metrics: tuple, labels: tuple):
    """Record the series of target; remove those of targets not scraped any more."""
    with _LOCK:
        for idle in _EXPIRY.use(target, time.monotonic()):
            for metric, values in _SERIES.pop(idle, ()):
                with contextlib.suppress(KeyError):
                    metric.remove(*values)
        _SERIES.setdefault(target, set()).update((metric, labels) for metric in metrics)


@contextlib.contextmanager
def measure_api_request(target: str, endpoint: str):
    """Measure one PVE API request, if api metrics are enabled."""
    if not API_METRICS_ENABLED:
        yield
        return
    _use(target, (API_ERRORS, API_DURATION), (target, endpoint))
    with API_ERRORS.labels(target, endpoint).count_exceptions(), \
            API_DURATION.labels(target, endpoint).time():
        yield


@contextlib.contextmanager
def measure_scrape(target: str, cluster: bool, node: bool):
    """Measure the scrape of a target, if target metrics are enabled."""
    if not TARGET_METRICS_ENABLED:
        yield
        return
    labels = (target, str(int(cluster)), str(int(node)))
    _use(target, (TARGET_ERRORS, TARGET_DURATION), labels)
    with TARGET_ERRORS.labels(*labels).count_exceptions(), \
            TARGET_DURATION.labels(*labels).time():
        yield
