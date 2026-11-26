"""Independent checks for per-location meter-bias calibration and sorting.

Run from the batch root with the standard library only:

    python3 test-artifacts/yangyang-pallet-mixture/tests/oracle.py -v

The suite reads the real domain modules (``backend/src/domain``) plus the CSV
serialiser, so it fails loudly if the implementation drifts from the reviewed
business rules.  It deliberately re-derives the expected verdicts from the
frozen component table instead of trusting the implementation's own helpers.
"""

import csv
import io
import sys
import unittest
from math import exp, log, pi, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))

from src.domain.calibration import (  # noqa: E402
    Calibration, changed_locations, normalize_calibrations, resolve_calibrations,
    snapshot, versions, reading_interval, interval_center)
from src.domain.fixtures import demo_rows  # noqa: E402
from src.domain.mixture import Component  # noqa: E402
from src.domain.project import analyze, build_result, compute, resume  # noqa: E402
from src.domain.records import normalize_rows, partition  # noqa: E402
from src.domain.selection import select_model  # noqa: E402
from src.domain.sorting import group_name, score_boards  # noqa: E402
from src.utils.csv_files import read_rows, write_rows  # noqa: E402

DEMO_SETTINGS = {'max_components': 3, 'min_posterior': 0.85, 'max_component_z': 3.5}

# The reviewed demo fixture: 144 training boards, BIC 2 components at 8.4151%
# and 21.0494%.  These values appear in BUSINESS.md and must not move.
DEMO_BIC_2 = 767.7146062842
DEMO_MEANS = (8.41513814577302, 21.04944227424469)


def components_from(means):
    """Equal-weight, unit-variance components for hand-computable checks."""
    return tuple(Component(1 / len(means), mean, 1.0) for mean in means)


def posterior_at(components, value):
    """Independent log-space posterior, written from the formula in BUSINESS.md."""
    terms = []
    for part in components:
        terms.append(log(part.weight) - 0.5 * log(2 * pi * part.variance)
                     - 0.5 * (value - part.mean) ** 2 / part.variance)
    peak = max(terms)
    scaled = [exp(term - peak) for term in terms]
    total = sum(scaled)
    return [item / total for item in scaled]


def nearest_distance(components, value):
    return min(abs(value - part.mean) / sqrt(part.variance) for part in components)


class LedgerAndModelTests(unittest.TestCase):
    """Baseline model values the rest of the feature is layered on."""

    def test_demo_partition_and_selection(self):
        train, holdout, pending = partition(demo_rows())
        self.assertEqual((len(train), len(holdout), len(pending)), (144, 48, 19))
        selected, candidates = select_model([row['moisture_pct'] for row in train], 3)
        self.assertEqual(selected.size, 2)
        self.assertAlmostEqual(selected.bic, DEMO_BIC_2, places=8)
        for part, mean in zip(selected.fit.components, DEMO_MEANS):
            self.assertAlmostEqual(part.mean, mean, places=8)
        by_size = {item.size: item for item in candidates}
        self.assertAlmostEqual(by_size[1].bic, 958.4881621468, places=8)
        self.assertAlmostEqual(by_size[3].bic, 778.2899599351, places=8)

    def test_uncalibrated_analysis_matches_point_rules(self):
        result = analyze(demo_rows(), {})
        train, _holdout, pending = partition(demo_rows())
        components = select_model([row['moisture_pct'] for row in train], 3)[0].fit.components
        expected = {}
        for row in pending:
            probabilities = posterior_at(components, row['moisture_pct'])
            leader = max(range(len(probabilities)), key=probabilities.__getitem__)
            reasons = []
            if probabilities[leader] < DEMO_SETTINGS['min_posterior']:
                reasons.append('ambiguous_posterior')
            if nearest_distance(components, row['moisture_pct']) > DEMO_SETTINGS['max_component_z']:
                reasons.append('outside_components')
            if row['location'] not in {item['location'] for item in train}:
                reasons.append('unseen_location')
            expected[row['id']] = ('retest' if reasons else f'sort_{group_name(leader, 2)}',
                                   '|'.join(reasons))
        queue = next(table for table in result['tables'] if table['key'] == 'sorting_queue')
        got = {row['id']: (row['action'], row['reason_codes']) for row in queue['rows']}
        self.assertEqual(got, expected)
        self.assertEqual(result['metrics']['人工复测板件数'], 9)

    def test_csv_round_trip_is_stable(self):
        rows = demo_rows()
        text = write_rows(list(rows[0]), rows)
        reread = read_rows(text, list(rows[0]))
        self.assertEqual(normalize_rows(reread), normalize_rows(rows))


class CalibrationRevisionTests(unittest.TestCase):
    """Largest revision wins, regardless of arrival order."""

    def test_latest_revision_wins_in_any_order(self):
        records = [
            {'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 1},
            {'location': 'RACK-A', 'bias_lower_pct': -1.0, 'bias_upper_pct': 1.0, 'revision': 4},
            {'location': 'RACK-A', 'bias_lower_pct': -0.8, 'bias_upper_pct': 0.8, 'revision': 3},
        ]
        variants = [records, list(reversed(records)), [records[1], records[0], records[2]]]
        for order in variants:
            resolved = resolve_calibrations(order)
            self.assertEqual(resolved['RACK-A'].revision, 4)
            self.assertEqual(reading_interval(10.0, resolved['RACK-A']), (9.0, 11.0))

    def test_late_older_revision_is_ignored(self):
        resolved = resolve_calibrations([
            {'location': 'RACK-B', 'bias_lower_pct': -2.0, 'bias_upper_pct': 2.0, 'revision': 5},
            {'location': 'RACK-B', 'bias_lower_pct': -9.0, 'bias_upper_pct': 9.0, 'revision': 2},
        ])
        self.assertEqual(resolved['RACK-B'].revision, 5)
        self.assertEqual(snapshot(resolved)['RACK-B']['bias_upper_pct'], 2.0)

    def test_conflicting_duplicate_revision_rejected(self):
        with self.assertRaises(ValueError):
            normalize_calibrations([
                {'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 2},
                {'location': 'RACK-A', 'bias_lower_pct': -0.6, 'bias_upper_pct': 0.6, 'revision': 2},
            ])
        identical = normalize_calibrations([
            {'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 2},
            {'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 2},
        ])
        self.assertEqual(len(identical), 1)

    def test_bias_and_revision_bounds(self):
        for record in (
            {'location': 'R', 'bias_lower_pct': 2.0, 'bias_upper_pct': 1.0, 'revision': 1},
            {'location': 'R', 'bias_lower_pct': -101, 'bias_upper_pct': 1, 'revision': 1},
            {'location': 'R', 'bias_lower_pct': -1, 'bias_upper_pct': 1, 'revision': 0},
            {'location': 'R', 'bias_lower_pct': -1, 'bias_upper_pct': 1, 'revision': 1.5},
            {'location': 'R', 'bias_lower_pct': float('nan'), 'bias_upper_pct': 1, 'revision': 1},
            {'location': '-bad', 'bias_lower_pct': -1, 'bias_upper_pct': 1, 'revision': 1},
        ):
            with self.assertRaises(ValueError):
                normalize_calibrations([record])

    def test_reading_interval_subtracts_bounds(self):
        calibration = Calibration('RACK-A', -0.75, 1.25, 7)
        low, high = reading_interval(14.0, calibration)
        self.assertEqual((low, high), (14.0 - 1.25, 14.0 + 0.75))
        center, half = interval_center(14.0, calibration)
        self.assertAlmostEqual(center, 14.0 - 0.25)
        self.assertAlmostEqual(half, 1.0)

    def test_versions_and_changed_locations(self):
        before = snapshot(resolve_calibrations([
            {'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 1},
            {'location': 'RACK-B', 'bias_lower_pct': -1.0, 'bias_upper_pct': 1.0, 'revision': 2},
        ]))
        after = snapshot(resolve_calibrations([
            {'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 1},
            {'location': 'RACK-B', 'bias_lower_pct': -1.5, 'bias_upper_pct': 1.5, 'revision': 3},
        ]))
        self.assertEqual(versions(before), {'RACK-A': 1, 'RACK-B': 2})
        self.assertEqual(changed_locations(before, after), ['RACK-B'])
        self.assertEqual(changed_locations(after, after), [])
        self.assertEqual(changed_locations({'RACK-A': 1}, {}), ['RACK-A'])


class IntervalVerdictTests(unittest.TestCase):
    """Whole interval must sit in one zone; both ends agreeing is not enough."""

    def setUp(self):
        self.options = {'min_posterior': 0.55, 'max_component_z': 8.0}

    def _board(self, value, location='RACK-A'):
        return {'id': 'B-1', 'lot_id': 'LOT', 'location': location, 'split': 'pending',
                'moisture_pct': value}

    def test_interval_inside_one_zone_sorts(self):
        components = components_from([8.0, 21.0])
        calibration = Calibration('RACK-A', -0.4, 0.4, 1)
        scored = score_boards([self._board(20.6)], components, self.options,
                              {'RACK-A'}, {'RACK-A': calibration})[0]
        self.assertEqual(scored['action'], 'sort_wet')
        self.assertEqual(scored['reason_codes'], '')

    def test_interval_crossing_zone_boundary_retests(self):
        components = components_from([8.0, 21.0])
        calibration = Calibration('RACK-A', -6.0, 6.0, 1)
        scored = score_boards([self._board(14.5)], components, self.options,
                              {'RACK-A'}, {'RACK-A': calibration})[0]
        self.assertEqual(scored['action'], 'retest')
        self.assertIn('calibration_span', scored['reason_codes'].split('|'))
        self.assertEqual(scored['interval_groups'], 'dry|wet')

    def test_same_endpoints_but_middle_switches_zone(self):
        """Endpoints agree while the middle changes zone.

        A very wide, flat component sits under a narrow one.  Both ends of the
        interval are dominated by the flat component, yet the narrow component
        wins in the middle: the "两端同向而中间改道" case that endpoint-only
        checking would sort automatically.
        """
        components = (Component(0.5, 8.0, 400.0), Component(0.5, 12.0, 0.16))
        calibration = Calibration('RACK-A', -32.5, 32.5, 1)
        scored = score_boards([self._board(12.5)], components, self.options,
                              {'RACK-A'}, {'RACK-A': calibration})[0]
        self.assertIn('calibration_span', scored['reason_codes'].split('|'))
        self.assertEqual(scored['action'], 'retest')

        def group_at(value):
            return score_boards([self._board(value)], components, self.options, {'RACK-A'}, {})[0]['suggested_group']

        ends = [group_at(scored['interval_lower_pct']), group_at(scored['interval_upper_pct'])]
        middle = group_at((scored['interval_lower_pct'] + scored['interval_upper_pct']) / 2)
        self.assertEqual(ends[0], ends[1])
        self.assertNotEqual(middle, ends[0])

    def test_interval_without_calibration_uses_point_rule(self):
        components = components_from([8.0, 21.0])
        scored = score_boards([self._board(9.0)], components, self.options, {'RACK-A'}, {})[0]
        self.assertEqual(scored['action'], 'sort_dry')
        self.assertEqual(scored['interval_lower_pct'], scored['interval_upper_pct'])

    def test_unseen_location_retests_even_with_calibration(self):
        components = components_from([8.0, 21.0])
        calibration = Calibration('RACK-NEW', -0.2, 0.2, 1)
        scored = score_boards([self._board(9.0, 'RACK-NEW')], components, self.options,
                              {'RACK-A'}, {'RACK-NEW': calibration})[0]
        self.assertEqual(scored['action'], 'retest')
        self.assertIn('unseen_location', scored['reason_codes'].split('|'))

    def test_all_triggering_reasons_are_kept(self):
        components = components_from([8.0, 21.0])
        calibration = Calibration('RACK-A', -6.0, 6.0, 1)
        scored = score_boards([self._board(14.5)], components, dict(self.options, min_posterior=0.99),
                              {'RACK-A'}, {'RACK-A': calibration})[0]
        codes = set(scored['reason_codes'].split('|'))
        self.assertTrue({'ambiguous_posterior', 'calibration_span'} <= codes)

    def test_outside_components_uses_worst_point_in_interval(self):
        components = components_from([8.0, 21.0])
        calibration = Calibration('RACK-A', -2.0, 2.0, 1)
        scored = score_boards([self._board(9.0)], components, self.options,
                              {'RACK-A'}, {'RACK-A': calibration})[0]
        self.assertEqual(scored['action'], 'sort_dry')
        tight = score_boards([self._board(9.0)], components, dict(self.options, max_component_z=1.5),
                             {'RACK-A'}, {'RACK-A': calibration})[0]
        self.assertIn('outside_components', tight['reason_codes'].split('|'))


class RecomputeTests(unittest.TestCase):
    """Only changed locations are rescored; frozen slices stay byte-identical."""

    def _queue(self, result):
        return {row['id']: row for table in result['tables'] if table['key'] == 'sorting_queue'
                for row in table['rows']}

    def test_initial_calibration_records_every_location(self):
        records = [
            {'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 1},
            {'location': 'RACK-B', 'bias_lower_pct': -1.5, 'bias_upper_pct': 1.0, 'revision': 2},
        ]
        result, state = compute(demo_rows(), {}, records)
        pinned = {item['location']: item['revision'] for item in result['calibration']['locations']}
        self.assertEqual(pinned, {'RACK-A': 1, 'RACK-B': 2})
        self.assertEqual(set(result['calibration']['recomputed']), {'RACK-A', 'RACK-B'})
        self.assertTrue(result['calibration']['complete'])
        self.assertIn('interval_lower_pct', next(t for t in result['tables']
                                                 if t['key'] == 'sorting_queue')['columns'])

    def test_resume_only_touches_changed_location(self):
        records = [
            {'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 1},
            {'location': 'RACK-B', 'bias_lower_pct': -1.5, 'bias_upper_pct': 1.0, 'revision': 2},
        ]
        first, state = compute(demo_rows(), {}, records)
        later = snapshot(resolve_calibrations(records + [
            {'location': 'RACK-B', 'bias_lower_pct': -2.5, 'bias_upper_pct': 1.5, 'revision': 3}]))
        targets = changed_locations(snapshot(resolve_calibrations(records)), later)
        self.assertEqual(targets, ['RACK-B'])
        scored, frozen = resume(state, later, targets)
        second = build_result(frozen, scored, later, recomputed=targets)
        self.assertEqual(second['calibration']['recomputed'], ['RACK-B'])
        before, after = self._queue(first), self._queue(second)
        for board_id, row in before.items():
            if row['location'] != 'RACK-B':
                self.assertEqual(row, after[board_id], board_id)
        self.assertEqual(after['RC-P02-002']['calibration_revision'], 3)

    def test_resume_equals_full_recompute(self):
        records = [
            {'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 1},
            {'location': 'RACK-B', 'bias_lower_pct': -1.5, 'bias_upper_pct': 1.0, 'revision': 2},
        ]
        _first, state = compute(demo_rows(), {}, records)
        later = records + [
            {'location': 'RACK-A', 'bias_lower_pct': -1.0, 'bias_upper_pct': 1.0, 'revision': 2},
            {'location': 'RACK-B', 'bias_lower_pct': -2.5, 'bias_upper_pct': 1.5, 'revision': 3},
        ]
        resolved = snapshot(resolve_calibrations(later))
        targets = changed_locations(snapshot(resolve_calibrations(records)), resolved)
        scored, frozen = resume(state, resolved, targets)
        incremental = build_result(frozen, scored, resolved, recomputed=targets)
        full = analyze(demo_rows(), {}, later)
        self.assertEqual(self._queue(incremental), self._queue(full))
        self.assertEqual(incremental['metrics'], full['metrics'])

    def test_late_older_calculation_is_detected_as_stale(self):
        """A run pinned on revision 1 must be flagged once revision 2 arrives."""
        records = [{'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 1}]
        old, _state = compute(demo_rows(), {}, records)
        pinned = versions(old['calibration']['locations'])
        newer = snapshot(resolve_calibrations(records + [
            {'location': 'RACK-A', 'bias_lower_pct': -1.0, 'bias_upper_pct': 1.0, 'revision': 2}]))
        self.assertEqual(changed_locations(pinned, versions(newer)), ['RACK-A'])
        self.assertEqual(versions(old['calibration']['locations']), {'RACK-A': 1})

    def test_analyze_without_calibration_has_no_calibration_block(self):
        self.assertNotIn('calibration', analyze(demo_rows(), {}))
        self.assertNotIn('bias_lower_pct', next(
            t for t in analyze(demo_rows(), {})['tables'] if t['key'] == 'sorting_queue')['columns'])


class ReportTests(unittest.TestCase):
    """CSV exports keep their declared column order and round-trip."""

    def test_calibrated_export_columns_are_declared(self):
        records = [{'location': 'RACK-A', 'bias_lower_pct': -0.5, 'bias_upper_pct': 0.5, 'revision': 1}]
        result = analyze(demo_rows(), {}, records)
        for key in ('sorting_queue', 'holdout_scoring'):
            table = next(item for item in result['tables'] if item['key'] == key)
            text = write_rows(table['columns'], table['rows'])
            reader = csv.DictReader(io.StringIO(text.removeprefix('﻿')))
            self.assertEqual(reader.fieldnames, table['columns'])
            self.assertEqual(len(list(reader)), len(table['rows']))

    def test_uncalibrated_board_columns_unchanged(self):
        result = analyze(demo_rows(), {})
        queue = next(table for table in result['tables'] if table['key'] == 'sorting_queue')
        self.assertEqual(queue['columns'], [
            'id', 'lot_id', 'location', 'split', 'moisture_pct', 'component', 'suggested_group',
            'posterior', 'runner_up_component', 'runner_up_posterior', 'log_density', 'nearest_z',
            'location_seen_in_train', 'action', 'reason_codes'])


if __name__ == '__main__':
    unittest.main()
