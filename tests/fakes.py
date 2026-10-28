"""
Stand-ins for the PVE API and helpers to read collected metrics in tests.
"""

from prometheus_client.parser import text_string_to_metric_families


class FakeAPI:
    """
    Stand-in for proxmoxer.ProxmoxAPI: attribute and call chains build the
    path, get() answers from routes {path: data or callable(params)}. Every
    request is recorded in requests as (path, params).
    """

    def __init__(self, routes, path=(), requests=None):
        self.routes = routes
        self._path = path
        self.requests = [] if requests is None else requests

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return FakeAPI(self.routes, self._path + (name,), self.requests)

    def __call__(self, *parts):
        return FakeAPI(self.routes, self._path + tuple(str(part) for part in parts),
                       self.requests)

    def get(self, **params):  # pylint: disable=missing-function-docstring
        path = '/'.join(self._path)
        self.requests.append((path, params))
        if path not in self.routes:
            raise AssertionError(f'unexpected request {path} {params}')
        answer = self.routes[path]
        if isinstance(answer, Exception):
            raise answer
        return answer(params) if callable(answer) else answer


def samples(families):
    """Flatten metric families into {(name, frozenset(labels)): value}."""
    return {
        (sample.name, frozenset(sample.labels.items())): sample.value
        for family in families
        for sample in family.samples
    }


def parsed(text):
    """Flatten Prometheus text format into {(name, frozenset(labels)): value}."""
    return samples(text_string_to_metric_families(text))


def series(result, name):
    """Return {frozenset(labels): value} of one metric."""
    return {labels: value for (metric, labels), value in result.items() if metric == name}


def ids(result, name):
    """Return the ids of one metric."""
    return {dict(labels)['id'] for labels in series(result, name)}
