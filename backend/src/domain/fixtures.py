"""Reproducible mixed-stock ledger with whole-lot holdout and live queue."""

from random import Random

DEMO_SEED = 261107


def demo_rows() -> list[dict]:
    random = Random(DEMO_SEED)
    lots = (
        ('RC-T01', 'RACK-A', 'train', 32, 16, 0.0),
        ('RC-T02', 'RACK-B', 'train', 16, 32, 0.5),
        ('RC-T03', 'RACK-A', 'train', 24, 24, -0.3),
        ('RC-H01', 'RACK-B', 'holdout', 12, 12, 0.8),
        ('RC-H02', 'RACK-C', 'holdout', 10, 14, 2.4),
    )
    rows = []
    for lot, location, split, dry, wet, offset in lots:
        readings = [random.gauss(8.6 + offset, 1.55) for _ in range(dry)]
        readings.extend(random.gauss(20.8 + offset, 1.85) for _ in range(wet))
        random.shuffle(readings)
        rows.extend(_lot(lot, location, split, readings))
    rows.extend(_lot('RC-P01', 'RACK-A', 'pending', (7.9, 9.2, 13.8, 14.4, 15.0, 19.7, 22.3, 34.0)))
    rows.extend(_lot('RC-P02', 'RACK-B', 'pending', (1.0, 8.5, 12.9, 14.2, 16.0, 19.1, 23.9, 28.0)))
    rows.extend(_lot('RC-P03', 'RACK-C', 'pending', (8.7, 14.3, 20.0)))
    return rows


def _lot(lot: str, location: str, split: str, readings) -> list[dict]:
    return [{'id': f'{lot}-{number:03d}', 'lot_id': lot, 'location': location,
             'split': split, 'moisture_pct': round(reading, 2)}
            for number, reading in enumerate(readings, start=1)]
