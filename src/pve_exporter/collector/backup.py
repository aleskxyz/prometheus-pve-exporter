"""
Prometheus collectors for Proxmox VE backups: backup jobs and the results of
vzdump tasks.
"""
# pylint: disable=too-few-public-methods

import threading
import time
import typing
from dataclasses import dataclass

from prometheus_client.core import GaugeMetricFamily

from pve_exporter.collector.common import GUEST_TYPES, ReadFailures, flag, label
from pve_exporter.util import IDLE_SECONDS, IdleExpiry, parse_bool


# How far back schedule-analyze is asked for past runs: the window grows by
# SCHEDULE_WINDOW_FACTOR from the first to the last value until two past runs
# are found, so that frequent schedules need few runs and rare ones few calls.
SCHEDULE_WINDOW_FIRST = 3600
SCHEDULE_WINDOW_FACTOR = 4
SCHEDULE_WINDOW_MAX = 800 * 86400
# How long a schedule without a next run (e.g. a date in the past) is not
# asked again.
SCHEDULE_NO_NEXT_TTL = 3600
# schedule-analyze returns at most this many runs per call.
SCHEDULE_ITERATIONS = 100
# Calls one schedule may take, e.g. a schedule of every minute in January,
# asked in December, pages through a month of runs.
SCHEDULE_MAX_CALLS = 500


@dataclass
class ScheduledRuns:
    """The two newest past runs and the next run of a schedule."""
    previous: int | None
    last: int | None
    next: int | None


class _Analyze:
    """schedule-analyze calls for one schedule, at most SCHEDULE_MAX_CALLS."""

    def __init__(self, pve, schedule: str):
        self._pve = pve
        self._schedule = schedule
        self._calls = 0

    def runs_until(self, start: int, end: int) -> tuple[list[int], int | None]:
        """The runs after start up to end, and the first run after end (None if there is none)."""
        runs_until_end = []
        while True:
            self._calls += 1
            if self._calls > SCHEDULE_MAX_CALLS:
                raise RuntimeError(f'schedule {self._schedule!r} needs more than '
                                   f'{SCHEDULE_MAX_CALLS} schedule-analyze calls')
            # an attribute, not an id, so that its endpoint names it
            runs = [int(run['timestamp']) for run in getattr(
                self._pve.cluster.jobs, 'schedule-analyze').get(
                    schedule=self._schedule, starttime=start, iterations=SCHEDULE_ITERATIONS)]
            runs_until_end.extend(run for run in runs if run <= end)
            after = next((run for run in runs if run > end), None)
            if after is not None or len(runs) < SCHEDULE_ITERATIONS or runs[-1] <= start:
                return runs_until_end, after
            # every run of this page is up to end: continue after the last
            start = runs[-1]


def scheduled_runs(pve, schedule: str, now: int) -> ScheduledRuns:
    """
    Return the runs of a calendar event around now, as computed by PVE
    (/cluster/jobs/schedule-analyze), so that PVE's calendar logic and
    timezone apply. The window into the past grows until it holds two runs;
    each growth asks only for the part not covered yet.
    """
    analyze = _Analyze(pve, schedule)
    window = SCHEDULE_WINDOW_FIRST
    past, upcoming = analyze.runs_until(now - window, now)
    while len(past) < 2 and window < SCHEDULE_WINDOW_MAX:
        covered = now - window
        window = min(window * SCHEDULE_WINDOW_FACTOR, SCHEDULE_WINDOW_MAX)
        older, _ = analyze.runs_until(now - window, covered)
        past = older + past
    past = past[-2:]
    return ScheduledRuns(
        previous=past[0] if len(past) == 2 else None,
        last=past[-1] if past else None,
        next=upcoming)


class ScheduleCache:
    """
    Scheduled runs per (scope, schedule), valid until the next run, or for
    SCHEDULE_NO_NEXT_TTL when there is none. A scope is a module and target,
    as their credentials may see different jobs; a scope not used for
    idle_seconds is forgotten.
    """

    def __init__(self, idle_seconds: float = IDLE_SECONDS):
        self._lock = threading.Lock()
        self._runs = {}
        self._expiry = IdleExpiry(idle_seconds)

    def get(self, pve, scope: typing.Hashable, schedule: str, now: int) -> ScheduledRuns:
        """Return the cached runs, or ask PVE once they are outdated."""
        key = (scope, schedule)
        with self._lock:
            runs, valid_until = self._runs.get(key, (None, 0))
        if runs is None or valid_until <= now:
            runs = scheduled_runs(pve, schedule, now)
            valid_until = runs.next if runs.next is not None else now + SCHEDULE_NO_NEXT_TTL
            with self._lock:
                self._runs[key] = (runs, valid_until)
        return runs

    def retain(self, scope: typing.Hashable, schedules: set[str]):
        """Forget the schedules of scope that are not in schedules, and idle scopes."""
        with self._lock:
            idle = set(self._expiry.use(scope, time.monotonic()))
            for key in [key for key in self._runs
                        if key[0] in idle or (key[0] == scope and key[1] not in schedules)]:
                del self._runs[key]


SCHEDULES = ScheduleCache()


class BackupJobsCollector:
    """
    Collects the backup jobs (/cluster/backup), the guests each job selects
    and the job's scheduled runs. E.g.:

    pve_backup_job_info{job_id="backup-1",storage="pbs1",schedule="sat 01:00",...} 1.0
    pve_backup_job_enabled{job_id="backup-1"} 1.0
    pve_backup_job_last_scheduled_timestamp_seconds{job_id="backup-1"} 1.7e9
    pve_backup_job_guest_info{job_id="backup-1",storage="pbs1",id="qemu/100",vmid="100"} 1.0
    """

    def __init__(self, pve, scope: typing.Hashable, schedules: ScheduleCache | None = None):
        self._pve = pve
        self._scope = scope
        self._schedules = SCHEDULES if schedules is None else schedules
        self.partial_failure = None

    def collect(self):  # pylint: disable=missing-docstring,too-many-locals
        job_info = GaugeMetricFamily(
            'pve_backup_job_info',
            ('Backup job (/cluster/backup), always 1. all, pool and node are the job\'s own '
             'selection keys; the guests it selects are pve_backup_job_guest_info.'),
            labels=['job_id', 'storage', 'schedule', 'mode', 'node', 'all', 'pool',
                    'repeat_missed'])
        enabled = GaugeMetricFamily(
            'pve_backup_job_enabled', 'Whether the backup job is enabled.', labels=['job_id'])
        previous = GaugeMetricFamily(
            'pve_backup_job_previous_scheduled_timestamp_seconds',
            ('The scheduled run before the last one of an enabled backup job: the second '
             'newest run not in the future that /cluster/jobs/schedule-analyze returns.'),
            labels=['job_id'])
        last = GaugeMetricFamily(
            'pve_backup_job_last_scheduled_timestamp_seconds',
            ('The newest scheduled run of an enabled backup job that is not in the future, as '
             '/cluster/jobs/schedule-analyze returns it.'),
            labels=['job_id'])
        upcoming = GaugeMetricFamily(
            'pve_backup_job_next_run_timestamp_seconds',
            'The next run of the backup job (next-run of /cluster/backup).',
            labels=['job_id'])
        guest_info = GaugeMetricFamily(
            'pve_backup_job_guest_info',
            'A guest the backup job selects (as PVE resolves all, pool, vmid, exclude and node), '
            'always 1.',
            labels=['job_id', 'storage', 'id', 'vmid'])
        excluded = GaugeMetricFamily(
            'pve_backup_job_guest_excluded_volumes',
            ('Volumes of a selected guest the backup job does not back up; omitted when there '
             'are none.'),
            labels=['job_id', 'id'])
        unknown = GaugeMetricFamily(
            'pve_backup_job_unknown_guest_info',
            'A VMID the backup job lists that is no guest (any more), always 1. The job\'s '
            'tasks end with errors.',
            labels=['job_id', 'vmid'])

        families = {
            'info': job_info, 'enabled': enabled, 'previous': previous, 'last': last,
            'upcoming': upcoming, 'guest': guest_info, 'excluded': excluded, 'unknown': unknown,
        }
        now = int(time.time())
        jobs = self._pve.cluster.backup.get()
        failures = ReadFailures('backup jobs')
        for job in jobs:
            # a job whose guests or schedule can not be read is left out as a
            # whole, so that no job looks as if it selected no guests
            samples = failures.read(label(job.get('id')),
                                    lambda job=job: self._job_samples(job, now))
            for family, values, value in samples or ():
                families[family].add_metric(values, value)
        self._schedules.retain(self._scope, {
            str(job['schedule']) for job in jobs
            if parse_bool(job.get('enabled', 1)) and job.get('schedule')})
        self.partial_failure = failures.summary()
        yield from families.values()

    def _job_samples(self, job: dict, now: int) -> list[tuple[str, list[str], float]]:
        """The samples of one job as (family key, labels, value)."""
        job_id = label(job['id'])
        storage = label(job.get('storage'))
        out = [('info', [
            job_id, storage, label(job.get('schedule')), label(job.get('mode')),
            label(job.get('node')), flag(job.get('all')), label(job.get('pool')),
            flag(job.get('repeat-missed'))], 1)]
        is_enabled = parse_bool(job.get('enabled', 1))
        out.append(('enabled', [job_id], is_enabled))
        if job.get('next-run'):
            out.append(('upcoming', [job_id], int(job['next-run'])))
        if is_enabled and job.get('schedule'):
            runs = self._schedules.get(self._pve, self._scope, str(job['schedule']), now)
            for family, value in (('previous', runs.previous), ('last', runs.last)):
                if value is not None:
                    out.append((family, [job_id], value))

        volumes = self._pve.cluster.backup(job_id).included_volumes.get()
        for guest in volumes.get('children', []):
            vmid = label(guest['id'])
            if guest.get('type') not in GUEST_TYPES:
                out.append(('unknown', [job_id, vmid], 1))
                continue
            guest_id = f"{guest['type']}/{vmid}"
            out.append(('guest', [job_id, storage, guest_id, vmid], 1))
            excluded_volumes = sum(
                1 for volume in guest.get('children', []) if not parse_bool(volume.get('included')))
            if excluded_volumes:
                out.append(('excluded', [job_id, guest_id], excluded_volumes))
        return out


# vzdump tasks listed per node, newest first: enough to hold the running ones
# and the newest finished one.
TASK_LIST_LIMIT = 50


def task_succeeded(status: str) -> bool:
    """Whether a task status means success (OK, or OK with warnings)."""
    return status == 'OK' or status.startswith('WARNINGS')


def task_finished(task: dict) -> bool:
    """Whether a task of the task list has ended."""
    return bool(task.get('endtime')) and task.get('status') not in (None, '', 'RUNNING')


class BackupTasksCollector:
    """
    Collects the vzdump tasks of every online node, as the node task list
    (/nodes/{node}/tasks?typefilter=vzdump) reports them: the newest finished
    task and the running ones. The task list tells neither the storage nor
    the guests of a task, and task logs are not read. E.g.:

    pve_backup_task_last_start_timestamp_seconds{node="pve1"} 1.7e9
    pve_backup_task_last_success{node="pve1"} 1.0
    pve_backup_tasks_running{node="pve1"} 0.0
    """

    def __init__(self, pve):
        self._pve = pve
        self.partial_failure = None

    def collect(self):  # pylint: disable=missing-docstring
        last_start = GaugeMetricFamily(
            'pve_backup_task_last_start_timestamp_seconds',
            'Start of the newest finished vzdump task of the node.', labels=['node'])
        last_end = GaugeMetricFamily(
            'pve_backup_task_last_end_timestamp_seconds',
            'End of the newest finished vzdump task of the node.', labels=['node'])
        last_success = GaugeMetricFamily(
            'pve_backup_task_last_success',
            'Whether the newest finished vzdump task of the node ended without error (OK or with '
            'warnings); a task with errors for some guests ends with "job errors".',
            labels=['node'])
        running = GaugeMetricFamily(
            'pve_backup_tasks_running',
            'Running vzdump tasks of the node, including those waiting for the backup lock.',
            labels=['node'])
        running_start = GaugeMetricFamily(
            'pve_backup_task_running_start_timestamp_seconds',
            'Start of the oldest running vzdump task of the node.', labels=['node'])

        failures = ReadFailures('vzdump tasks of nodes')
        for entry in self._pve.cluster.status.get():
            if entry['type'] != 'node' or not entry.get('online'):
                continue
            node = entry['name']
            tasks = failures.read(node, lambda node=node: self._pve.nodes(node).tasks.get(
                typefilter='vzdump', source='all', limit=TASK_LIST_LIMIT))
            if tasks is None:
                continue
            finished = [task for task in tasks if task_finished(task)]
            active = [int(task['starttime']) for task in tasks if not task_finished(task)]
            if finished:
                newest = max(finished, key=lambda task: int(task['starttime']))
                last_start.add_metric([node], int(newest['starttime']))
                last_end.add_metric([node], int(newest['endtime']))
                last_success.add_metric([node], task_succeeded(newest['status']))
            running.add_metric([node], len(active))
            if active:
                running_start.add_metric([node], min(active))

        self.partial_failure = failures.summary()
        yield from (last_start, last_end, last_success, running, running_start)
