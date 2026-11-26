"""Domain integration for the pallet-moisture ledger and background worker."""

from .fixtures import demo_rows
from .records import normalize_rows, partition
from .reports import board_table, candidate_table, component_table, location_table
from .selection import select_model
from .settings import SPEC, parse_settings
from .sorting import location_diagnostics, score_boards

__all__ = ['SPEC', 'demo_rows', 'normalize_rows', 'analyze']


def analyze(rows: list[dict], settings: dict) -> dict:
    options = parse_settings(settings)
    train, holdout, pending = partition(rows)
    selected, candidates = select_model([row['moisture_pct'] for row in train], options['max_components'])
    components = selected.fit.components
    trained_locations = {row['location'] for row in train}
    train_scores = score_boards(train, components, options, trained_locations)
    holdout_scores = score_boards(holdout, components, options, trained_locations)
    queue = score_boards(pending, components, options, trained_locations)
    manual_count = sum(row['action'] == 'retest' for row in queue)
    holdout_mean = sum(row['log_density'] for row in holdout_scores) / len(holdout) if holdout else None
    warnings = []
    if selected.size == 1:
        warnings.append('BIC 选择单成分，待分拣板件全部转人工复测。')
    if not holdout:
        warnings.append('台账未提供独立留出批次，缺少跨批密度诊断。')
    if not pending:
        warnings.append('台账未提供待分拣板件。')
    if any(not row['location_seen_in_train'] for row in holdout_scores + queue):
        warnings.append('留出或待分拣台账包含训练未覆盖的库位，待分拣板件须人工复测。')
    if any(item.attempts and not item.converged_starts for item in candidates):
        warnings.append('有候选成分数的全部起点未收敛，可在 BIC 表查看状态。')
    return {
        'summary': (f'使用 {len(train)} 件训练板材拟合，BIC 选择 {selected.size} 个成分。'
                    f'留出密度诊断覆盖 {len(holdout)} 件，{len(queue)} 件待分拣板材中 {manual_count} 件需人工复测。'),
        'metrics': {'训练板件数': len(train), '留出板件数': len(holdout), '待分拣板件数': len(queue),
                    '选中成分数': selected.size, '选中 BIC': round(selected.bic, 10),
                    '留出平均对数密度': round(holdout_mean, 10) if holdout_mean is not None else None,
                    '人工复测板件数': manual_count},
        'tables': [board_table('sorting_queue', '待分拣板件与人工复测单', queue),
                   candidate_table(candidates, selected, train), component_table(selected, train, options),
                   board_table('holdout_scoring', '整批留出板件评分', holdout_scores),
                   location_table(location_diagnostics(train_scores + holdout_scores + queue))],
        'warnings': warnings,
    }
