"""Convert density scores to review actions while preserving the raw evidence.

Each board is scored inside the moisture interval produced by its location's
meter-bias calibration.  The original point rules still decide the zone, but a
board is only sorted automatically when *every* point of its interval falls in
the same zone.  Interval endpoints alone are not enough: for mixtures whose
components have different variances the leading component can change in the
middle of the interval while both ends agree ("两端同向而中间改道").  Such a
board is sent to retest with the ``calibration_span`` reason.
"""

from collections import defaultdict
from math import log, sqrt

from .calibration import as_calibration, reading_interval
from .mixture import Component, evaluate

REASON_ORDER = ('single_component', 'ambiguous_posterior', 'outside_components',
                'calibration_span', 'unseen_location')
CONFIDENCE_SAMPLES = 32
TINY = 1e-14


def group_name(index: int, total: int) -> str:
    if total == 1:
        return 'single'
    if index == 0:
        return 'dry'
    if index == total - 1:
        return 'wet'
    return 'intermediate'


def score_boards(rows: list[dict], components: tuple[Component, ...], options: dict,
                 trained_locations: set[str], calibrations: dict | None = None) -> list[dict]:
    calibrations = calibrations or {}
    return [_score_board(row, components, options, trained_locations, calibrations) for row in rows]


def _point(components: tuple[Component, ...], value: float):
    log_density, probabilities = evaluate(components, value)
    ranked = sorted(range(len(probabilities)), key=lambda index: (-probabilities[index], index))
    leader = ranked[0]
    confidence = probabilities[leader]
    runner_up = ranked[1] if len(ranked) > 1 else None
    nearest_z = min(abs(value - part.mean) / sqrt(part.variance) for part in components)
    return leader, confidence, runner_up, probabilities, log_density, nearest_z


def _leader(components: tuple[Component, ...], value: float) -> int:
    return _point(components, value)[0]


def _confidence(components: tuple[Component, ...], value: float, leader: int) -> float:
    return _point(components, value)[1]


def _quadratic_roots(a: float, b: float, c: float) -> list[float]:
    if abs(a) < TINY:
        if abs(b) < TINY:
            return []
        return [-c / b]
    discriminant = b * b - 4 * a * c
    if discriminant < 0:
        return []
    if discriminant == 0:
        return [-b / (2 * a)]
    root = sqrt(discriminant)
    q = -0.5 * (b + root) if b >= 0 else -0.5 * (b - root)
    if q == 0:
        return [-b / (2 * a)]
    return [q / a, c / q]


def _pair_roots(left: Component, right: Component, offset: float = 0.0) -> list[float]:
    """Roots of ``log(w_left f_left) - log(w_right f_right) = offset``."""
    va, vb = left.variance, right.variance
    a = -1 / (2 * va) + 1 / (2 * vb)
    b = left.mean / va - right.mean / vb
    c = (-left.mean ** 2 / (2 * va) + right.mean ** 2 / (2 * vb)
         + log(left.weight / right.weight) - 0.5 * log(va / vb) - offset)
    return _quadratic_roots(a, b, c)


def _bisect_confidence(left: float, right: float, leader: int,
                       components: tuple[Component, ...], threshold: float) -> float:
    value_left = _confidence(components, left, leader) - threshold
    for _ in range(60):
        middle = (left + right) / 2
        value_middle = _confidence(components, middle, leader) - threshold
        if value_left == 0 or right - left < 1e-12:
            return middle
        if (value_left < 0) != (value_middle < 0):
            right = middle
        else:
            left, value_left = middle, value_middle
    return (left + right) / 2


def _confidence_roots(left: float, right: float, leader: int,
                      components: tuple[Component, ...], threshold: float) -> list[float]:
    """Crossings of the leading posterior below/above the review threshold.

    With two components the crossing is the root of a quadratic and is found
    exactly.  With more components the posterior has no closed form, so the
    segment is scanned on a fine grid and every sign change is bisected.
    """
    if threshold <= 0 or threshold >= 1:
        return []
    if len(components) == 2:
        other = 1 - leader
        ratio = threshold / (1 - threshold)
        return [root for root in _pair_roots(components[leader], components[other], log(ratio))
                if left < root < right]
    step = (right - left) / CONFIDENCE_SAMPLES
    if step <= 0:
        return []
    roots = []
    previous = _confidence(components, left, leader) - threshold
    for index in range(1, CONFIDENCE_SAMPLES + 1):
        value = left + index * step
        current = _confidence(components, value, leader) - threshold
        if previous == 0:
            roots.append(left + (index - 1) * step)
        elif (previous < 0) != (current < 0):
            roots.append(_bisect_confidence(left + (index - 1) * step, value, leader,
                                            components, threshold))
        previous = current
    return roots


def _critical_points(low: float, high: float, components: tuple[Component, ...],
                     options: dict) -> list[float]:
    """Points where the leading component, confidence or distance rule may switch.

    Between two consecutive critical points the leader is constant, the posterior
    threshold predicate cannot change and the nearest-distance predicate cannot
    change, so evaluating the point rule at midpoints is exhaustive.
    """
    points = {low, high}
    for first in range(len(components)):
        for second in range(first + 1, len(components)):
            for root in _pair_roots(components[first], components[second]):
                if low < root < high:
                    points.add(root)
    for part in components:
        spread = options['max_component_z'] * sqrt(part.variance)
        for root in (part.mean - spread, part.mean + spread):
            if low < root < high:
                points.add(root)
    ordered = sorted(points)
    extra = []
    for left, right in zip(ordered, ordered[1:]):
        if right <= left:
            continue
        leader = _leader(components, (left + right) / 2)
        for root in _confidence_roots(left, right, leader, components, options['min_posterior']):
            if left < root < right:
                extra.append(root)
    return sorted(points.union(extra))


def _probes(low: float, high: float, components: tuple[Component, ...], options: dict) -> list[float]:
    points = _critical_points(low, high, components, options)
    probes = list(points)
    probes.extend((left + right) / 2 for left, right in zip(points, points[1:]))
    return sorted(set(probes))


def _interval_reasons(probes, components, options):
    """Union of point-rule reasons plus the component/group sets seen."""
    reasons = set()
    leaders = set()
    groups = set()
    for value in probes:
        leader, confidence, _runner_up, _probabilities, _density, nearest_z = _point(components, value)
        leaders.add(leader + 1)
        groups.add(group_name(leader, len(components)))
        if len(components) == 1:
            reasons.add('single_component')
        if confidence < options['min_posterior']:
            reasons.add('ambiguous_posterior')
        if nearest_z > options['max_component_z']:
            reasons.add('outside_components')
    return reasons, leaders, groups


def _ordered_groups(leaders, components) -> list[str]:
    ordered = []
    for index in sorted(leader - 1 for leader in leaders):
        name = group_name(index, len(components))
        if name not in ordered:
            ordered.append(name)
    return ordered


def _score_board(row, components, options, trained_locations, calibrations):
    value = row['moisture_pct']
    calibration = calibrations.get(row['location'])
    if calibration is not None:
        calibration = as_calibration(calibration, row['location'])
    if calibration is None:
        low = high = value
    else:
        low, high = reading_interval(value, calibration)
    center = (low + high) / 2
    leader, confidence, runner_up, probabilities, log_density, nearest_z = _point(components, center)
    seen = row['location'] in trained_locations

    reasons = set()
    if high > low:
        probes = _probes(low, high, components, options)
        interval_reasons, leaders, groups = _interval_reasons(probes, components, options)
        reasons |= interval_reasons
        if len(groups) > 1:
            reasons.add('calibration_span')
    else:
        leaders, groups = {leader + 1}, {group_name(leader, len(components))}
        if len(components) == 1:
            reasons.add('single_component')
        if confidence < options['min_posterior']:
            reasons.add('ambiguous_posterior')
        if nearest_z > options['max_component_z']:
            reasons.add('outside_components')
    if not seen:
        reasons.add('unseen_location')

    if len(groups) == 1:
        group = next(iter(groups))
    else:
        group = group_name(leader, len(components))
    ordered = [code for code in REASON_ORDER if code in reasons]
    return {**row, 'component': leader + 1, 'suggested_group': group,
            'posterior': confidence, 'runner_up_component': runner_up + 1 if runner_up is not None else None,
            'runner_up_posterior': probabilities[runner_up] if runner_up is not None else None,
            'log_density': log_density, 'nearest_z': nearest_z, 'location_seen_in_train': seen,
            'bias_lower_pct': calibration.bias_lower_pct if calibration is not None else None,
            'bias_upper_pct': calibration.bias_upper_pct if calibration is not None else None,
            'calibration_revision': calibration.revision if calibration is not None else None,
            'interval_lower_pct': low, 'interval_upper_pct': high,
            'interval_uniform': len(groups) == 1,
            'interval_groups': '|'.join(_ordered_groups(leaders, components)),
            'action': 'retest' if ordered else f'sort_{group}', 'reason_codes': '|'.join(ordered)}


def location_diagnostics(rows: list[dict]) -> list[dict]:
    locations = defaultdict(list)
    for row in rows:
        locations[row['split'], row['location']].append(row)
    diagnostics = []
    for (split, location), boards in sorted(locations.items()):
        count = len(boards)
        retest_count = sum(board['action'] == 'retest' for board in boards)
        diagnostics.append({'split': split, 'location': location,
                            'lots': len({board['lot_id'] for board in boards}), 'boards': count,
                            'mean_moisture_pct': sum(board['moisture_pct'] for board in boards) / count,
                            'mean_log_density': sum(board['log_density'] for board in boards) / count,
                            'retest_count': retest_count, 'retest_share': retest_count / count,
                            'calibration_revision': boards[0]['calibration_revision'],
                            'location_seen_in_train': boards[0]['location_seen_in_train']})
    return diagnostics
