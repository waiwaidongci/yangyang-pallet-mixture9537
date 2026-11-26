"""Convert density scores to review actions while preserving the raw evidence."""

from collections import defaultdict
from math import sqrt

from .mixture import Component, evaluate


def group_name(index: int, total: int) -> str:
    if total == 1:
        return 'single'
    if index == 0:
        return 'dry'
    if index == total - 1:
        return 'wet'
    return 'intermediate'


def score_boards(rows: list[dict], components: tuple[Component, ...], options: dict,
                 trained_locations: set[str]) -> list[dict]:
    return [_score_board(row, components, options, trained_locations) for row in rows]


def _score_board(row, components, options, trained_locations):
    value = row['moisture_pct']
    log_density, probabilities = evaluate(components, value)
    ranked = sorted(range(len(probabilities)), key=probabilities.__getitem__, reverse=True)
    leader = ranked[0]
    confidence = probabilities[leader]
    runner_up = ranked[1] if len(ranked) > 1 else None
    nearest_z = min(abs(value - part.mean) / sqrt(part.variance) for part in components)
    seen = row['location'] in trained_locations
    reasons = []
    if len(components) == 1:
        reasons.append('single_component')
    if confidence < options['min_posterior']:
        reasons.append('ambiguous_posterior')
    if nearest_z > options['max_component_z']:
        reasons.append('outside_components')
    if not seen:
        reasons.append('unseen_location')
    group = group_name(leader, len(components))
    return {**row, 'component': leader + 1, 'suggested_group': group,
            'posterior': confidence, 'runner_up_component': runner_up + 1 if runner_up is not None else None,
            'runner_up_posterior': probabilities[runner_up] if runner_up is not None else None,
            'log_density': log_density, 'nearest_z': nearest_z, 'location_seen_in_train': seen,
            'action': 'retest' if reasons else f'sort_{group}', 'reason_codes': '|'.join(reasons)}


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
                            'location_seen_in_train': boards[0]['location_seen_in_train']})
    return diagnostics
