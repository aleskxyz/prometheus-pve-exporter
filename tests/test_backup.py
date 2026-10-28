"""
Tests for the Proxmox VE backup collectors.
"""

import itertools
import unittest
from unittest import mock

from fakes import FakeAPI, samples, series

from pve_exporter.collector import backup
from pve_exporter.collector.backup import (
    BackupJobsCollector,
    BackupTasksCollector,
    ScheduleCache,
    scheduled_runs,
)
from pve_exporter.collector.scrape_api import TargetUnreachable

DAY = 86400
WEEK = 7 * DAY
# Saturday 2026-10-17 01:00 UTC
SAT = 1792198800
NOW = SAT + 3 * DAY

def weekly(params):
    """schedule-analyze for "sat 01:00": runs strictly after starttime."""
    start = int(params['starttime'])
    first = SAT + ((start - SAT) // WEEK + 1) * WEEK
    return [{'timestamp': first + i * WEEK, 'utc': ''} for i in range(params['iterations'])]


class ScheduledRunsTest(unittest.TestCase):
    """The runs around now come from PVE's schedule-analyze."""

    def test_weekly(self):
        api = FakeAPI({'cluster/jobs/schedule-analyze': weekly})
        runs = scheduled_runs(api, 'sat 01:00', NOW)
        self.assertEqual((runs.previous, runs.last, runs.next), (SAT - WEEK, SAT, SAT + WEEK))

    def test_on_the_run(self):
        api = FakeAPI({'cluster/jobs/schedule-analyze': weekly})
        runs = scheduled_runs(api, 'sat 01:00', SAT)
        self.assertEqual((runs.last, runs.next), (SAT, SAT + WEEK))

    def test_minutely(self):
        def every_minute(params):
            start = int(params['starttime'])
            return [{'timestamp': start + 60 * (i + 1)} for i in range(params['iterations'])]
        api = FakeAPI({'cluster/jobs/schedule-analyze': every_minute})
        runs = scheduled_runs(api, 'minutely', NOW)
        self.assertEqual(runs.next - runs.last, 60)
        self.assertEqual(runs.last - runs.previous, 60)
        self.assertLessEqual(runs.last, NOW)
        self.assertGreater(runs.next, NOW)

    def test_few_calls(self):
        calls = []

        def counting(schedule):
            def answer(params):
                calls.append(params)
                return schedule(params)
            return answer

        def every_minute(params):
            start = int(params['starttime'])
            return [{'timestamp': start + 60 * (i + 1)} for i in range(params['iterations'])]
        scheduled_runs(FakeAPI({'cluster/jobs/schedule-analyze': counting(every_minute)}), 'minutely', NOW)
        self.assertEqual(len(calls), 1)
        calls.clear()
        scheduled_runs(FakeAPI({'cluster/jobs/schedule-analyze': counting(weekly)}), 'sat 01:00', NOW)
        # one call per window: 1 h (the next run), 4 h, 16 h, 64 h and 256 h (the two past runs)
        self.assertEqual(len(calls), 5)

    def test_growing_window_asks_only_for_the_part_not_covered(self):
        api = FakeAPI({'cluster/jobs/schedule-analyze': weekly})
        scheduled_runs(api, 'sat 01:00', NOW)
        starts = [params['starttime'] for _, params in api.requests]
        self.assertEqual(starts, [NOW - 3600 * 4 ** i for i in range(5)])

    def test_bursty_schedule_pages_forward(self):
        """A run every minute on Mondays, asked on Thursday: the window pages through Monday."""
        monday = SAT + 2 * DAY

        def mondays_every_minute(params):
            run, runs = (int(params['starttime']) // 60 + 1) * 60, []
            while len(runs) < params['iterations']:
                if (run - monday) // DAY % 7 == 0:
                    runs.append({'timestamp': run})
                    run += 60
                else:
                    run = monday + ((run - monday) // WEEK + 1) * WEEK
            return runs
        api = FakeAPI({'cluster/jobs/schedule-analyze': mondays_every_minute})
        thursday = monday + 3 * DAY + 12 * 3600
        runs = scheduled_runs(api, 'mon *:*', thursday)
        self.assertEqual((runs.previous, runs.last, runs.next),
                         (monday + DAY - 120, monday + DAY - 60, monday + WEEK))
        # 1 h, 4 h and 16 h: nothing; 64 h: Monday from 20:00, in three pages
        self.assertEqual(len(api.requests), 6)

    def test_too_many_calls(self):
        def every_second(params):
            start = int(params['starttime'])
            return [{'timestamp': start + i + 1} for i in range(params['iterations']) if start + i + 1 < NOW - DAY]
        api = FakeAPI({'cluster/jobs/schedule-analyze': every_second})
        with mock.patch.object(backup, 'SCHEDULE_MAX_CALLS', 20), \
                self.assertRaisesRegex(RuntimeError, 'more than 20 schedule-analyze calls'):
            scheduled_runs(api, 'every second until yesterday', NOW)
        self.assertEqual(len(api.requests), 20)

    def test_no_runs(self):
        api = FakeAPI({'cluster/jobs/schedule-analyze': []})
        runs = scheduled_runs(api, '2020-01-01 00:00', NOW)
        self.assertEqual((runs.previous, runs.last, runs.next), (None, None, None))

    def test_yearly_widens_the_window(self):
        def yearly(params):
            start = int(params['starttime'])
            year = 365 * DAY
            first = SAT + ((start - SAT) // year + 1) * year
            return [{'timestamp': first + i * year} for i in range(params['iterations'])]
        api = FakeAPI({'cluster/jobs/schedule-analyze': yearly})
        runs = scheduled_runs(api, 'yearly', NOW)
        self.assertEqual((runs.previous, runs.last), (SAT - 365 * DAY, SAT))



JOBS = [
    {'id': 'backup-weekly', 'type': 'vzdump', 'schedule': 'sat 01:00', 'storage': 'pbs1',
     'all': 1, 'exclude': '102', 'enabled': 1, 'mode': 'snapshot'},
    {'id': 'backup-off', 'type': 'vzdump', 'schedule': 'sat 01:00', 'storage': 'pbs2',
     'pool': 'prod', 'enabled': 0, 'next-run': SAT + WEEK},
]

INCLUDED = {
    'backup-weekly': {'children': [
        {'id': 100, 'type': 'qemu', 'name': 'vm100', 'children': [
            {'id': '100:scsi0', 'included': 1, 'reason': 'backup=no'},
            {'id': '100:scsi1', 'included': 0, 'reason': 'backup=no'}]},
        {'id': 200, 'type': 'lxc', 'name': 'ct200', 'children': [
            {'id': '200:rootfs', 'included': 1, 'reason': 'rootfs'}]},
        {'id': 999, 'type': 'unknown', 'leaf': 1},
    ]},
    'backup-off': {'children': [{'id': 100, 'type': 'qemu', 'children': []}]},
}



def jobs_api(**overrides):
    """A fake PVE with JOBS and INCLUDED."""
    routes = {
        'cluster/backup': JOBS,
        'cluster/backup/backup-weekly/included_volumes': INCLUDED['backup-weekly'],
        'cluster/backup/backup-off/included_volumes': INCLUDED['backup-off'],
        'cluster/jobs/schedule-analyze': weekly,
    }
    routes.update(overrides)
    return FakeAPI(routes)


class BackupJobsCollectorTest(unittest.TestCase):
    """Backup jobs, their guests and scheduled runs."""

    def setUp(self):
        self.schedules = ScheduleCache()

    def collect(self, api, scope='dc1'):
        """Collect at NOW."""
        with mock.patch('time.time', return_value=NOW):
            return samples(BackupJobsCollector(api, scope, self.schedules).collect())

    def test_jobs(self):
        result = self.collect(jobs_api())
        self.assertEqual(series(result, 'pve_backup_job_enabled'), {
            frozenset({('job_id', 'backup-weekly')}): 1,
            frozenset({('job_id', 'backup-off')}): 0,
        })
        info = {dict(labels)['job_id']: dict(labels) for labels in series(result, 'pve_backup_job_info')}
        self.assertEqual(info['backup-weekly']['all'], '1')
        self.assertEqual(info['backup-off']['all'], '0')
        self.assertEqual(info['backup-off']['pool'], 'prod')

    def test_scheduled_runs_only_for_enabled_jobs(self):
        result = self.collect(jobs_api())
        weekly_job = frozenset({('job_id', 'backup-weekly')})
        self.assertEqual(series(result, 'pve_backup_job_last_scheduled_timestamp_seconds'), {weekly_job: SAT})
        self.assertEqual(series(result, 'pve_backup_job_previous_scheduled_timestamp_seconds'),
                         {weekly_job: SAT - WEEK})
        # next-run as /cluster/backup reports it, also for the disabled job
        self.assertEqual(series(result, 'pve_backup_job_next_run_timestamp_seconds'),
                         {frozenset({('job_id', 'backup-off')}): SAT + WEEK})

    def test_guests(self):
        result = self.collect(jobs_api())
        self.assertEqual(set(series(result, 'pve_backup_job_guest_info')), {
            frozenset({('job_id', 'backup-weekly'), ('storage', 'pbs1'), ('id', 'qemu/100'), ('vmid', '100')}),
            frozenset({('job_id', 'backup-weekly'), ('storage', 'pbs1'), ('id', 'lxc/200'), ('vmid', '200')}),
            frozenset({('job_id', 'backup-off'), ('storage', 'pbs2'), ('id', 'qemu/100'), ('vmid', '100')}),
        })
        # only guests with excluded volumes
        self.assertEqual(series(result, 'pve_backup_job_guest_excluded_volumes'),
                         {frozenset({('job_id', 'backup-weekly'), ('id', 'qemu/100')}): 1})
        self.assertEqual(series(result, 'pve_backup_job_unknown_guest_info'),
                         {frozenset({('job_id', 'backup-weekly'), ('vmid', '999')}): 1})

    def test_schedule_is_cached_until_the_next_run(self):
        calls = []

        def counting(params):
            calls.append(params)
            return weekly(params)
        api = jobs_api(**{'cluster/jobs/schedule-analyze': counting})
        self.collect(api)
        first = len(calls)
        self.collect(api)
        self.assertEqual(len(calls), first)
        # after the next run, the runs are asked again
        with mock.patch('time.time', return_value=SAT + WEEK + 60):
            result = samples(BackupJobsCollector(api, 'dc1', self.schedules).collect())
        self.assertGreater(len(calls), first)
        self.assertEqual(series(result, 'pve_backup_job_last_scheduled_timestamp_seconds'),
                         {frozenset({('job_id', 'backup-weekly')}): SAT + WEEK})

    def test_schedule_without_runs_is_cached(self):
        calls = []

        def none(params):
            calls.append(params)
            return []
        api = jobs_api(**{'cluster/jobs/schedule-analyze': none})
        self.collect(api)
        first = len(calls)
        self.collect(api)
        self.assertEqual(len(calls), first)

    def test_schedules_of_removed_jobs_are_forgotten(self):
        api = jobs_api()
        self.collect(api)
        api.routes['cluster/backup'] = [dict(JOBS[0], schedule='sun 01:00')]
        self.collect(api)
        self.assertEqual(
            {schedule for _, schedule in self.schedules._runs},  # pylint: disable=protected-access
            {'sun 01:00'})

    def test_unreadable_job_is_left_out(self):
        collector = BackupJobsCollector(
            jobs_api(**{'cluster/backup/backup-off/included_volumes': RuntimeError('500')}), 'dc1',
            self.schedules)
        with mock.patch('time.time', return_value=NOW):
            result = samples(collector.collect())
        self.assertEqual({dict(labels)['job_id'] for labels in series(result, 'pve_backup_job_info')},
                         {'backup-weekly'})
        self.assertFalse(any(('job_id', 'backup-off') in labels for _, labels in result))
        self.assertIn('backup-off', collector.partial_failure)

    def test_enabled_as_string(self):
        jobs = [dict(JOBS[0], enabled='0')]
        result = self.collect(jobs_api(**{'cluster/backup': jobs}))
        self.assertEqual(series(result, 'pve_backup_job_enabled'),
                         {frozenset({('job_id', 'backup-weekly')}): 0})
        self.assertEqual(series(result, 'pve_backup_job_last_scheduled_timestamp_seconds'), {})

    def test_idle_scopes_are_forgotten(self):
        self.schedules = ScheduleCache(idle_seconds=1.5)
        # each scrape calls the clock once, one second later
        with mock.patch('time.monotonic', side_effect=itertools.count()):
            for scope in ('old', 'new', 'new'):
                self.collect(jobs_api(), scope)
        self.assertEqual({scope for scope, _ in self.schedules._runs},  # pylint: disable=protected-access
                         {'new'})

    def test_scope_returning_after_idle_keeps_its_schedules(self):
        """Runs are valid until the next run however long a scope was idle: they are not read again."""
        calls = []

        def counting(params):
            calls.append(params)
            return weekly(params)
        api = jobs_api(**{'cluster/jobs/schedule-analyze': counting})
        self.schedules = ScheduleCache(idle_seconds=10)
        with mock.patch('time.monotonic', side_effect=[0, 100, 200]):
            self.collect(api)
            first = len(calls)
            self.collect(api)
            self.collect(api)
        self.assertEqual(len(calls), first)

    def test_scopes_keep_their_schedules(self):
        """Modules whose credentials see different jobs do not evict each other's schedules."""
        calls = []

        def counting(params):
            calls.append(params)
            return weekly(params)
        admin = jobs_api(**{'cluster/jobs/schedule-analyze': counting})
        limited = jobs_api(**{'cluster/jobs/schedule-analyze': counting,
                              'cluster/backup': [dict(JOBS[0], schedule='sun 01:00')],
                              'cluster/backup/backup-weekly/included_volumes': {'children': []}})
        self.collect(admin, ('admin', 'pve1'))
        self.collect(limited, ('limited', 'pve1'))
        first = len(calls)
        for _ in range(3):
            self.collect(admin, ('admin', 'pve1'))
            self.collect(limited, ('limited', 'pve1'))
        self.assertEqual(len(calls), first)

    def test_unreachable_target_fails_the_collector(self):
        collector = BackupJobsCollector(
            jobs_api(**{'cluster/backup/backup-off/included_volumes': TargetUnreachable('gone')}),
            'dc1', self.schedules)
        with self.assertRaises(TargetUnreachable), mock.patch('time.time', return_value=NOW):
            list(collector.collect())


def task_list(tasks):
    """
    The node task list as PVE answers it: source active (running), archive
    (finished) or all, newest first, at most limit tasks.
    """
    if isinstance(tasks, Exception):
        return tasks

    def answer(params):
        finished = {'active': False, 'archive': True}.get(params.get('source', 'archive'))
        listed = [task for task in tasks if finished is None
                  or bool(task.get('endtime') and task.get('status') not in (None, '', 'RUNNING')) == finished]
        listed.sort(key=lambda task: -task['starttime'])
        return listed[:int(params.get('limit', 50))]
    return answer


def tasks_api(pve1_tasks, pve2_tasks):
    """A fake PVE with two online nodes and an offline one, and their vzdump tasks."""
    return FakeAPI({
        'cluster/status': [
            {'type': 'cluster', 'name': 'dc1'},
            {'type': 'node', 'name': 'pve1', 'online': 1},
            {'type': 'node', 'name': 'pve2', 'online': 1},
            {'type': 'node', 'name': 'pve3', 'online': 0},
        ],
        'nodes/pve1/tasks': task_list(pve1_tasks),
        'nodes/pve2/tasks': task_list(pve2_tasks),
    })


class BackupTasksCollectorTest(unittest.TestCase):
    """The newest finished and the running vzdump tasks per node, from the task list."""

    def test_tasks(self):
        api = tasks_api(
            [{'upid': 'u-run', 'starttime': SAT + DAY},
             {'upid': 'u-queued', 'starttime': SAT + DAY + 60, 'status': 'RUNNING'},
             {'upid': 'u-ok', 'starttime': SAT, 'endtime': SAT + 200, 'status': 'OK'},
             {'upid': 'u-old', 'starttime': SAT - WEEK, 'endtime': SAT - WEEK + 9, 'status': 'job errors'}],
            [{'upid': 'u-err', 'starttime': SAT + 60, 'endtime': SAT + 3800, 'status': 'job errors'},
             {'upid': 'u-warn', 'starttime': SAT - DAY, 'endtime': SAT - DAY + 9, 'status': 'WARNINGS: 1'}])
        result = samples(BackupTasksCollector(api).collect())
        self.assertEqual(api.requests[1:], [
            (f'nodes/{node}/tasks', {'typefilter': 'vzdump', 'source': source, 'limit': limit})
            for node in ('pve1', 'pve2') for source, limit in (('active', 1000), ('archive', 1))])
        pve1, pve2 = frozenset({('node', 'pve1')}), frozenset({('node', 'pve2')})
        self.assertEqual(series(result, 'pve_backup_task_last_start_timestamp_seconds'),
                         {pve1: SAT, pve2: SAT + 60})
        self.assertEqual(series(result, 'pve_backup_task_last_end_timestamp_seconds'),
                         {pve1: SAT + 200, pve2: SAT + 3800})
        self.assertEqual(series(result, 'pve_backup_task_last_success'), {pve1: 1, pve2: 0})
        self.assertEqual(series(result, 'pve_backup_tasks_running'), {pve1: 2, pve2: 0})
        self.assertEqual(series(result, 'pve_backup_task_running_start_timestamp_seconds'),
                         {pve1: SAT + DAY})

    def test_many_running_tasks(self):
        """More running tasks than one page of the task list: all are counted, the newest finished is kept."""
        running = [{'upid': f'u-{i}', 'starttime': SAT + i} for i in range(60)]
        finished = [{'upid': 'u-ok', 'starttime': SAT - DAY, 'endtime': SAT - DAY + 9, 'status': 'OK'}]
        collector = BackupTasksCollector(tasks_api(running + finished, []))
        result = samples(collector.collect())
        pve1 = frozenset({('node', 'pve1')})
        self.assertEqual(series(result, 'pve_backup_tasks_running')[pve1], 60)
        self.assertEqual(series(result, 'pve_backup_task_running_start_timestamp_seconds')[pve1], SAT)
        self.assertEqual(series(result, 'pve_backup_task_last_start_timestamp_seconds')[pve1], SAT - DAY)
        self.assertIsNone(collector.partial_failure)

    def test_too_many_running_tasks_are_reported(self):
        running = [{'upid': f'u-{i}', 'starttime': SAT + i} for i in range(5)]
        collector = BackupTasksCollector(tasks_api(running, []))
        with mock.patch.object(backup, 'ACTIVE_TASK_LIMIT', 5):
            list(collector.collect())
        self.assertEqual(collector.partial_failure,
                         'nodes pve1 have 5 or more running vzdump tasks, more than are listed')

    def test_warnings_are_success(self):
        api = tasks_api([{'upid': 'u', 'starttime': SAT, 'endtime': SAT + 9, 'status': 'WARNINGS: 2'}], [])
        result = samples(BackupTasksCollector(api).collect())
        self.assertEqual(series(result, 'pve_backup_task_last_success'), {frozenset({('node', 'pve1')}): 1})

    def test_unreadable_node_fails_the_collector(self):
        collector = BackupTasksCollector(tasks_api([], RuntimeError('500')))
        result = samples(collector.collect())
        self.assertEqual(series(result, 'pve_backup_tasks_running'), {frozenset({('node', 'pve1')}): 0})
        self.assertEqual(collector.partial_failure,
                         'vzdump tasks of nodes pve2 unreadable (last error: 500)')

    def test_unreachable_target_fails_the_collector(self):
        collector = BackupTasksCollector(tasks_api([], TargetUnreachable('gone')))
        with self.assertRaises(TargetUnreachable):
            list(collector.collect())


if __name__ == '__main__':
    unittest.main()
