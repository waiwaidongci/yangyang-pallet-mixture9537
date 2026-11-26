"""Append-only per-rack meter-bias calibration and the revision it resolves to.

The sorting team keeps an already reviewed mixture model after a rack changes
its moisture meter.  Each rack declares the instrument bias as an inclusive
interval.  Records are appended as they arrive and are never rewritten, so a
late correction is compared by revision instead of arrival order.
"""

from fastapi import HTTPException
from sqlalchemy import select

from src.domain.calibration import normalize_calibrations, resolve_calibrations, snapshot, versions
from src.models.calibration import LocationCalibration
from src.services.audit import record
from src.utils.time import utc_stamp


def _as_record(row: LocationCalibration) -> dict:
    return {'location': row.location, 'bias_lower_pct': row.bias_lower_pct,
            'bias_upper_pct': row.bias_upper_pct, 'revision': row.revision}


def stored_records(session) -> list[dict]:
    """Every persisted calibration row, deliberately in no meaningful order."""
    return [_as_record(row) for row in session.scalars(
        select(LocationCalibration).order_by(LocationCalibration.id))]


def current(session) -> dict[str, dict]:
    return snapshot(resolve_calibrations(stored_records(session)))


def current_versions(session) -> dict[str, int]:
    return versions(current(session))


def list_locations(session) -> list[dict]:
    resolved = resolve_calibrations(stored_records(session))
    winning = {location: item.revision for location, item in resolved.items()}
    return [{'location': row.location, 'bias_lower_pct': row.bias_lower_pct,
             'bias_upper_pct': row.bias_upper_pct, 'revision': row.revision,
             'note': row.note, 'created_at': row.created_at,
             'current': winning.get(row.location) == row.revision}
            for row in session.scalars(select(LocationCalibration).order_by(
                LocationCalibration.location, LocationCalibration.revision))]


def submit(session, records, actor=None, note: str = '') -> dict:
    """Append calibration records; conflicting duplicate revisions are rejected."""
    normalized = normalize_calibrations(records)
    if not normalized:
        raise ValueError('请至少提交一条库位仪表校准记录。')
    existing = {(row['location'], row['revision']): row for row in stored_records(session)}
    pending = []
    for item in normalized:
        previous = existing.get((item.location, item.revision))
        if previous is not None:
            if (previous['bias_lower_pct'], previous['bias_upper_pct']) != (item.bias_lower_pct, item.bias_upper_pct):
                raise HTTPException(409, f'库位 {item.location} 的修订号 {item.revision} 已存在不同内容的校准记录。')
            continue
        pending.append(item)
    stamp = utc_stamp()
    for item in pending:
        session.add(LocationCalibration(location=item.location, bias_lower_pct=item.bias_lower_pct,
                                        bias_upper_pct=item.bias_upper_pct, revision=item.revision,
                                        note=note, dataset_id=None,
                                        created_by=actor.id if actor is not None else None,
                                        created_at=stamp))
        record(session, 'calibration.recorded', item.revision, actor.id if actor is not None else None)
    session.commit()
    resolved = current(session)
    return {'accepted': len(pending), 'skipped': len(normalized) - len(pending),
            'locations': [{'location': location, 'bias_lower_pct': item['bias_lower_pct'],
                           'bias_upper_pct': item['bias_upper_pct'], 'revision': item['revision']}
                          for location, item in resolved.items()],
            'versions': versions(resolved)}
