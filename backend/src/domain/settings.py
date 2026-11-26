"""Moisture ledger schema and operator-configurable sorting thresholds."""

from math import isfinite

FIELDS = {
    'id': '板件编号',
    'lot_id': '来货批次',
    'location': '采样库位',
    'split': '样本用途',
    'moisture_pct': '仪表含水率（%）',
}
SETTINGS = [
    {'key': 'max_components', 'label': '候选最大成分数', 'default': 3, 'min': 2, 'max': 4, 'step': 1},
    {'key': 'min_posterior', 'label': '自动分流最低后验概率', 'default': 0.85,
     'min': 0.55, 'max': 0.99, 'step': 0.01},
    {'key': 'max_component_z', 'label': '最邻近成分最大标准差距离', 'default': 3.5,
     'min': 2, 'max': 6, 'step': 0.1},
]
SPEC = {
    'columns': list(FIELDS),
    'fields': FIELDS,
    'settings': SETTINGS,
    'modules': {
        'datasets': '来货板件测湿台账',
        'calibrations': '库位仪表偏差校准',
        'runs': '高斯混合拟合与 BIC 选型',
        'results': '库位对账与待分拣清单',
        'reviews': '复测交班签核',
    },
    'limitation': '干湿组表示该训练台账内的相对含水模式。跨库位的仪表差异须由各库位校准区间核对；'
                  '区间端点和中段都要落在同一相对湿度组才会自动分拣。',
    'dataset_name': '20261125-回收托盘板材测湿.csv',
    'recommendation': '先复测待分拣清单中的人工复测件，按最新校准核对跨组区间，再签核并切换分拣单。',
    'calibration_fields': {
        'location': '库位',
        'bias_lower_pct': '仪表偏差下限（百分点）',
        'bias_upper_pct': '仪表偏差上限（百分点）',
        'revision': '校准修订号',
    },
}


def parse_settings(raw: dict) -> dict[str, float | int]:
    if not isinstance(raw, dict) or raw.keys() - {item['key'] for item in SETTINGS}:
        raise ValueError('存在本项目未定义的运行参数。')
    values = {}
    for item in SETTINGS:
        key = item['key']
        value = raw.get(key, item['default'])
        if type(value) not in (int, float) or not item['min'] <= value <= item['max'] or not isfinite(value):
            raise ValueError(f"{item['label']}须在 {item['min']} 至 {item['max']} 之间。")
        if item['step'] == 1:
            if value != int(value):
                raise ValueError(f"{item['label']}须为整数。")
            value = int(value)
        values[key] = value
    return values
