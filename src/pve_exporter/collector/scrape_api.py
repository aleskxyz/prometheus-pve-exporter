"""
The PVE API as one scrape uses it.
"""

import requests

from pve_exporter.scrape_metrics import measure_api_request

# Names of the ids that follow these path segments in endpoints.
_ID_NAMES = {'nodes': 'node', 'qemu': 'vmid', 'lxc': 'vmid', 'storage': 'storage'}


class TargetUnreachable(Exception):
    """A request could not reach the target, or an earlier one could not."""


def unreachable(error: Exception) -> bool:
    """
    Whether error tells that the target can not be reached: a connection
    error of requests (connect timeouts included, read timeouts not) or of the
    operating system (refused, reset, aborted). A TLS failure is no such
    error: the target answered, and trying again does not help.
    """
    if isinstance(error, requests.exceptions.SSLError):
        return False
    return isinstance(error, (requests.exceptions.ConnectionError, ConnectionError))


def endpoint(path: tuple) -> str:
    """
    The endpoint of a path, with its call arguments (the ids) replaced by a
    name, e.g. /nodes/{node}/qemu/{vmid}/config: a few per target instead of
    one per guest.
    """
    parts = []
    for step in path:
        if isinstance(step, tuple):
            name = _ID_NAMES.get(parts[-1] if parts else '', 'id')
            parts.extend(f'{{{name}}}' for _ in step)
        else:
            parts.append(step)
    return '/' + '/'.join(parts)


class ScrapeAPI:
    """
    Wraps a PVE client (proxmoxer.ProxmoxAPI) for one scrape: attribute and
    call chains build the path as with proxmoxer, and get() sends each
    (path, parameters) once, as several collectors read the same lists
    (e.g. /cluster/status, /cluster/resources). A failed request fails again
    for every later get() of the scrape without being sent again, so that a
    slow endpoint costs its timeout once. A request that can not reach the
    target is tried once more; when that fails too, it and every further
    request raise TargetUnreachable at once. Each request sent is measured per
    endpoint of target.
    """

    def __init__(self, api, target: str = ''):
        self._api = api
        self._target = target
        # {(path, params): (answer, None) or (None, error)}
        self._answers = {}
        self._unreachable = None

    def request(self, path: tuple, params: dict):
        """GET path (a tuple of attribute names and call arguments) with params."""
        if self._unreachable is not None:
            raise TargetUnreachable(f'target unreachable: {self._unreachable}')
        key = (path, tuple(sorted((name, str(value)) for name, value in params.items())))
        if key not in self._answers:
            try:
                self._answers[key] = (self._send_retrying(path, params), None)
            except TargetUnreachable:
                raise
            except Exception as error:  # pylint: disable=broad-except
                self._answers[key] = (None, error)
        answer, error = self._answers[key]
        if error is not None:
            raise error
        return answer

    def _send_retrying(self, path: tuple, params: dict):
        resource = self._api
        for step in path:
            resource = resource(*step) if isinstance(step, tuple) else getattr(resource, step)
        try:
            return self._send(resource, path, params)
        except Exception as error:  # pylint: disable=broad-except
            if not unreachable(error):
                raise
        try:
            return self._send(resource, path, params)
        except Exception as error:  # pylint: disable=broad-except
            if not unreachable(error):
                raise
            self._unreachable = error
            raise TargetUnreachable(f'target unreachable: {error}') from error

    def _send(self, resource, path: tuple, params: dict):
        with measure_api_request(self._target, endpoint(path)):
            return resource.get(**params)

    def __getattr__(self, name):
        return _Resource(self, ()).__getattr__(name)


class _Resource:
    """A path below ScrapeAPI."""

    def __init__(self, scrape: ScrapeAPI, path: tuple):
        self._scrape = scrape
        self._path = path

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return _Resource(self._scrape, self._path + (name,))

    def __call__(self, *parts):
        return _Resource(self._scrape, self._path + (tuple(str(part) for part in parts),))

    def get(self, **params):
        """GET this path, see ScrapeAPI.request."""
        return self._scrape.request(self._path, params)
