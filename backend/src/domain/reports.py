"""Tabular exports for review, model reconstruction and storage-site checks."""

from math import sqrt

from .sorting import group_name

BOARD_COLUMNS = {
    'id': '板件编号', 'lot_id': '来货批次', 'location': '库位', 'split': '样本用途',
    'moisture_pct': '含水率（%）', 'component': '首选成分', 'suggested_group': '首选相对湿度组',
    'posterior': '首选后验概率', 'runner_up_component': '次选成分', 'runner_up_posterior': '次选后验概率',
    'log_density': '对数密度', 'nearest_z': '最近成分标准差距离', 'location_seen_in_train': '库位参加训练',
    'action': '操作', 'reason_codes': '复测原因代码',
}


def _table(key: str, title: str, labels: dict, rows: list[dict]) -> dict:
    def format_row(row):
        return {column: round(row[column], 10) if isinstance(row[column], float) else row[column]
                for column in labels}

    return {'key': key, 'title': title, 'columns': list(labels), 'labels': labels,
            'rows': [format_row(row) for row in rows]}


def board_table(key: str, title: str, rows: list[dict]) -> dict:
    if key == 'sorting_queue':
        rows = sorted(rows, key=lambda row: (row['action'] != 'retest', row['lot_id'], row['id']))
    return _table(key, title, BOARD_COLUMNS, rows)


def candidate_table(candidates, selected, train: list[dict]) -> dict:
    labels = {'components': '候选成分数', 'parameters': '自由参数数', 'train_boards': '训练板件数',
              'train_lots': '训练批次数', 'starts': '起点数', 'converged_starts': '收敛起点数',
              'iterations': '最优起点迭代次数', 'train_log_likelihood': '训练总对数似然',
              'bic': 'BIC', 'delta_bic': '相对选中模型的 ΔBIC', 'status': '候选状态', 'selected': '是否选中'}
    lots = len({row['lot_id'] for row in train})
    rows = []
    for candidate in candidates:
        if not candidate.attempts:
            status = 'insufficient_distinct_readings'
        elif not candidate.converged_starts:
            status = 'no_converged_start'
        else:
            status = 'eligible'
        rows.append({'components': candidate.size, 'parameters': candidate.parameters, 'train_boards': len(train),
                     'train_lots': lots, 'starts': candidate.attempts, 'converged_starts': candidate.converged_starts,
                     'iterations': candidate.fit.iterations if candidate.fit else None,
                     'train_log_likelihood': candidate.fit.log_likelihood if candidate.fit else None,
                     'bic': candidate.bic, 'delta_bic': candidate.bic - selected.bic if candidate.bic is not None else None,
                     'status': status, 'selected': candidate is selected})
    return _table('bic_candidates', '训练样本候选 BIC', labels, rows)


def component_table(selected, train: list[dict], options: dict) -> dict:
    labels = {'component': '成分序号', 'relative_group': '相对湿度组', 'weight': '混合权重',
              'mean_pct': '均值（%）', 'stddev_pct': '标准差（百分点）', 'variance_pct2': '方差（百分点平方）',
              'train_lot_ids': '训练批次', 'train_locations': '训练库位',
              'min_posterior': '后验复测阈值', 'max_component_z': '距离复测阈值'}
    lots = '|'.join(sorted({row['lot_id'] for row in train}))
    locations = '|'.join(sorted({row['location'] for row in train}))
    rows = [{'component': index + 1, 'relative_group': group_name(index, selected.size), 'weight': part.weight,
             'mean_pct': part.mean, 'stddev_pct': sqrt(part.variance), 'variance_pct2': part.variance,
             'train_lot_ids': lots, 'train_locations': locations, 'min_posterior': options['min_posterior'],
             'max_component_z': options['max_component_z']}
            for index, part in enumerate(selected.fit.components)]
    return _table('model_components', '冻结模型成分与训练来源', labels, rows)


def location_table(rows: list[dict]) -> dict:
    labels = {'split': '样本用途', 'location': '库位', 'lots': '来货批数', 'boards': '板件数',
              'mean_moisture_pct': '平均含水率（%）', 'mean_log_density': '平均对数密度',
              'retest_count': '人工复测件数', 'retest_share': '人工复测比例',
              'location_seen_in_train': '库位参加训练'}
    return _table('location_diagnostics', '各库位读数与分流诊断', labels, rows)
