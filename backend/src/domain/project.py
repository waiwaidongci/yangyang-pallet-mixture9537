"""Domain integration for the pallet-moisture ledger and background worker.

The analysis is split into a calibration-independent preparation step (ledger
validation, lot partitioning, mixture/BIC selection) and a per-location scoring
step.  A recalculation after a rack replaces its meter therefore only rescored
the locations whose winning calibration revision changed; every other location
reuses the frozen slice captured by the reviewed run.
"""

from collections import defaultdict
from dataclasses import dataclass

from .calibration import resolve_calibrations
from .calibration import snapshot as calibration_snapshot
from .fixtures import demo_rows
from .mixture import Component
from .records import normalize_rows, partition
from .reports import board_table, candidate_table, component_table, location_table
from .selection import select_model
from .settings import SPEC, parse_settings
from .sorting import location_diagnostics, score_boards

__all__ = ['SPEC', 'demo_rows', 'normalize_rows', 'prepare', 'compute', 'resume', 'analyze']


@dataclass(frozen=True)
class Prepared:
    """Calibration-independent part of one analysis."""

    options: dict
    train: list[dict]
    holdout: list[dict]
    pending: list[dict]
    selected: object
    candidates: list
    trained_locations: set
    components: tuple


def prepare(rows: list[dict], settings: dict) -> Prepared:
    options = parse_settings(settings)
    train, holdout, pending = partition(rows)
    selected, candidates = select_model([row['moisture_pct'] for row in train], options['max_components'])
    return Prepared(options, train, holdout, pending, selected, candidates,
                    {row['location'] for row in train}, selected.fit.components)


def group_key(split: str, location: str) -> str:
    return f'{split}|{location}'


def split_key(key: str) -> tuple[str, str]:
    split, location = key.split('|', 1)
    return split, location


def board_groups(train: list[dict], holdout: list[dict], pending: list[dict]) -> dict[str, list[dict]]:
    """Rows keyed by ``split|location``; each key is one scoring slice."""
    groups = {}
    for split, rows in (('train', train), ('holdout', holdout), ('pending', pending)):
        by_location = defaultdict(list)
        for row in rows:
            by_location[row['location']].append(row)
        for location, members in by_location.items():
            groups[group_key(split, location)] = members
    return groups


def capture(prepared: Prepared) -> dict:
    """Freeze the reviewed model plus the unscored slices of one analysis."""
    slices = board_groups(prepared.train, prepared.holdout, prepared.pending)
    return {
        'components': [{'weight': part.weight, 'mean': part.mean, 'variance': part.variance}
                       for part in prepared.components],
        'options': dict(prepared.options),
        'trained_locations': sorted(prepared.trained_locations),
        'counts': {'train': len(prepared.train), 'holdout': len(prepared.holdout),
                   'pending': len(prepared.pending)},
        'size': prepared.selected.size,
        'bic': prepared.selected.bic,
        'tables': {'bic_candidates': candidate_table(prepared.candidates, prepared.selected, prepared.train),
                   'model_components': component_table(prepared.selected, prepared.train, prepared.options)},
        'slices': {key: list(members) for key, members in slices.items()},
    }


def score_group(state: dict, location: str, rows: list[dict], calibration=None) -> list[dict]:
    components = tuple(Component(part['weight'], part['mean'], part['variance'])
                       for part in state['components'])
    calibrations = {location: calibration} if calibration is not None else {}
    return score_boards(rows, components, state['options'], set(state['trained_locations']), calibrations)


def rescore(state: dict, calibration: dict, targets: set[str]) -> dict:
    """Score only the given locations; every other slice keeps its frozen rows."""
    scored = {}
    for key, members in state['slices'].items():
        _split, location = split_key(key)
        if location in targets:
            members = score_group(state, location, members, calibration.get(location))
        scored[key] = list(members)
    return scored


def build_result(state: dict, scored: dict, calibration: dict | None = None,
                 recomputed: list[str] | None = None, complete: bool = True) -> dict:
    calibration = calibration or {}
    train_scores = _merge(scored, 'train')
    holdout_scores = _merge(scored, 'holdout')
    queue = _merge(scored, 'pending')
    counts = state['counts']
    manual_count = sum(row['action'] == 'retest' for row in queue)
    holdout_mean = sum(row['log_density'] for row in holdout_scores) / len(holdout_scores) if holdout_scores else None
    warnings = []
    if state['size'] == 1:
        warnings.append('BIC 选择单成分，待分拣板件全部转人工复测。')
    if not counts['holdout']:
        warnings.append('台账未提供独立留出批次，缺少跨批密度诊断。')
    if not counts['pending']:
        warnings.append('台账未提供待分拣板件。')
    if any(not row['location_seen_in_train'] for row in holdout_scores + queue):
        warnings.append('留出或待分拣台账包含训练未覆盖的库位，待分拣板件须人工复测。')
    if calibration:
        spanned = sum(not row['interval_uniform'] for row in queue)
        if spanned:
            warnings.append(f'{spanned} 件待分拣板件的校准含水区间跨越相对湿度组，已转人工复测。')
        if not complete:
            warnings.append('本次计算期间校准记录又有更新，须按最新校准重算并复核后才能切换分拣单。')
    metrics = {'训练板件数': counts['train'], '留出板件数': counts['holdout'],
               '待分拣板件数': counts['pending'], '选中成分数': state['size'],
               '选中 BIC': round(state['bic'], 10),
               '留出平均对数密度': round(holdout_mean, 10) if holdout_mean is not None else None,
               '人工复测板件数': manual_count}
    if calibration:
        metrics['已校准库位数'] = len(calibration)
        metrics['区间跨组待复测板件数'] = sum(
            row['action'] == 'retest' and not row['interval_uniform'] for row in queue)
    calibrated = bool(calibration)
    tables = state['tables']
    result = {
        'summary': (f'使用 {counts["train"]} 件训练板材拟合，BIC 选择 {state["size"]} 个成分。'
                    f'留出密度诊断覆盖 {counts["holdout"]} 件，{counts["pending"]} 件待分拣板材中 {manual_count} 件需人工复测。'),
        'metrics': metrics,
        'tables': [board_table('sorting_queue', '待分拣板件与人工复测单', queue, calibrated),
                   tables['bic_candidates'], tables['model_components'],
                   board_table('holdout_scoring', '整批留出板件评分', holdout_scores, calibrated),
                   location_table(location_diagnostics(train_scores + holdout_scores + queue), calibrated)],
        'warnings': warnings,
    }
    if calibration or recomputed:
        result['calibration'] = {
            'locations': [{'location': location, 'bias_lower_pct': item['bias_lower_pct'],
                           'bias_upper_pct': item['bias_upper_pct'], 'revision': item['revision']}
                          for location, item in calibration.items()],
            'recomputed': sorted(set(recomputed or [])),
            'complete': complete,
        }
    return result


def compute(rows: list[dict], settings: dict, calibrations=None) -> tuple[dict, dict]:
    """Full computation: fit the mixture, score every location and freeze slices."""
    prepared = prepare(rows, settings)
    state = capture(prepared)
    resolved = resolve_calibrations(calibrations or {})
    scored = rescore(state, resolved, {split_key(key)[1] for key in state['slices']})
    frozen = {**state, 'slices': scored}
    result = build_result(frozen, scored, calibration_snapshot(resolved),
                          recomputed=sorted(resolved))
    return result, frozen


def resume(state: dict, calibration: dict, targets) -> tuple[dict, dict]:
    """Reuse frozen slices and only rescore the named locations."""
    scored = rescore(state, calibration or {}, set(targets))
    return scored, {**state, 'slices': scored}


def analyze(rows: list[dict], settings: dict, calibrations=None) -> dict:
    return compute(rows, settings, calibrations)[0]


def _merge(scored: dict, split: str) -> list[dict]:
    rows = [row for key, members in scored.items() if split_key(key)[0] == split for row in members]
    return sorted(rows, key=lambda row: row['id'])
