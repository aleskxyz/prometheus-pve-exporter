"""
Tests for the command line of the exporter.
"""

import unittest

from pve_exporter.cli import build_parser, options_from_args
from pve_exporter.collector import COLLECTORS, CollectorsOptions

# The flags and defaults of the collectors before they came from the registry.
FLAGS = {
    '--collector.status': True,
    '--collector.version': True,
    '--collector.node': True,
    '--collector.cluster': True,
    '--collector.resources': True,
    '--collector.backup-jobs': True,
    '--collector.storage-config': True,
    '--collector.guest-created': True,
    '--collector.backup-tasks': False,
    '--collector.qdevice': True,
    '--collector.config': True,
    '--collector.replication': True,
    '--collector.subscription': True,
}


def parse(*argv):
    """The collector options of a command line."""
    return options_from_args(build_parser().parse_args(list(argv)))


class CliTest(unittest.TestCase):
    """A flag per collector of the registry."""

    def test_flags_and_defaults(self):
        self.assertEqual({spec.flag: spec.default for spec in COLLECTORS}, FLAGS)
        self.assertEqual(parse(), CollectorsOptions())
        self.assertFalse(parse().sparse_states)

    def test_every_flag_switches_its_collector(self):
        for spec in COLLECTORS:
            with self.subTest(flag=spec.flag):
                self.assertFalse(getattr(parse(spec.flag.replace('--', '--no-', 1)), spec.name))
                self.assertTrue(getattr(parse(spec.flag), spec.name))

    def test_sparse_states(self):
        self.assertTrue(parse('--collector.resources.sparse-states').sparse_states)


if __name__ == '__main__':
    unittest.main()
