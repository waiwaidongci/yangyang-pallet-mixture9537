"""Parse board readings and enforce lot-level separation before fitting."""

import re
from math import isfinite

from .settings import FIELDS

SPLITS = ('train', 'holdout', 'pending')
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}')
MIN_TRAIN_ROWS = 12


def normalize_rows(rows: list[dict]) -> list[dict]:
    if not 1 <= len(rows) <= 5000:
        raise ValueError('台账须包含 1 至 5000 条板件记录。')
    normalized = []
    ids = set()
    lot_splits = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or row.keys() != FIELDS.keys():
            raise ValueError(f'第 {index} 条记录须包含台账规定的全部列。')
        identifiers = {key: str(row[key]).strip() if isinstance(row[key], str) else ''
                       for key in ('id', 'lot_id', 'location')}
        for key, value in identifiers.items():
            if not TOKEN.fullmatch(value):
                raise ValueError(f'第 {index} 条记录的{FIELDS[key]}须为 1 至 40 位字母、数字、下划线或连字符，首位为字母或数字。')
        board_id, lot_id = identifiers['id'], identifiers['lot_id']
        if board_id in ids:
            raise ValueError(f'板件编号重复：{board_id}。')
        ids.add(board_id)
        split = row['split'].strip() if isinstance(row['split'], str) else ''
        if split not in SPLITS:
            raise ValueError(f'板件 {board_id} 的用途须为 train、holdout 或 pending。')
        if lot_splits.setdefault(lot_id, split) != split:
            raise ValueError(f'来货批次 {lot_id} 不可跨样本用途，请按整批分配。')
        try:
            if type(row['moisture_pct']) not in (int, float, str):
                raise ValueError
            moisture = float(row['moisture_pct'])
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f'板件 {board_id} 的含水率须为 0 至 100 的有限数字。') from None
        if not isfinite(moisture) or not 0 <= moisture <= 100:
            raise ValueError(f'板件 {board_id} 的含水率须为 0 至 100 的有限数字。')
        normalized.append({**identifiers, 'split': split, 'moisture_pct': moisture})
    return normalized


def partition(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    groups = {split: [] for split in SPLITS}
    for row in sorted(normalize_rows(rows), key=lambda row: row['id']):
        groups[row['split']].append(row)
    train = groups['train']
    if len(train) < MIN_TRAIN_ROWS:
        raise ValueError(f'拟合至少需要 {MIN_TRAIN_ROWS} 件训练板材。')
    return train, groups['holdout'], groups['pending']
