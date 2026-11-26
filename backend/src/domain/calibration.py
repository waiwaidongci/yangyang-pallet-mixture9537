"""Per-location meter bias calibration and revision resolution.

After a rack (``location``) changes its moisture meter the sorting team keeps
using the mixture model that was fitted, reviewed and frozen on the old
readings.  Each location declares its instrument bias as an inclusive interval
in percentage points.  A board reading ``r`` therefore corresponds to the true
moisture interval

    [r - bias_upper_pct, r - bias_lower_pct]

Calibration records are append-only and each carries a positive revision
number.  Arrival order does not matter: for every location the record with the
largest revision is authoritative, so a late correction always beats an older
one, and a stale computation is never allowed to replace a newer one.
"""

from dataclasses import dataclass
from math import isfinite

from .records import TOKEN

BIAS_LIMIT = 100.0
MIN_REVISION = 1
MAX_REVISION = 1_000_000


@dataclass(frozen=True)
class Calibration:
    location: str
    bias_lower_pct: float
    bias_upper_pct: float
    revision: int

    @property
    def lower(self) -> float:
        return self.bias_lower_pct

    @property
    def upper(self) -> float:
        return self.bias_upper_pct

    @property
    def rev(self) -> int:
        return self.revision


def _number(value, label: str) -> float:
    if isinstance(value, bool) or type(value) not in (int, float, str):
        raise ValueError(f'仪表偏差{label}须为有限数字。')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'仪表偏差{label}须为有限数字。') from None
    if not isfinite(number) or not -BIAS_LIMIT <= number <= BIAS_LIMIT:
        raise ValueError(f'仪表偏差{label}须在 -{BIAS_LIMIT:g} 至 {BIAS_LIMIT:g} 百分点之间。')
    return number


def _revision(value) -> int:
    if isinstance(value, bool) or type(value) not in (int, float, str):
        raise ValueError('校准修订号须为正整数。')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('校准修订号须为正整数。') from None
    if not isfinite(number) or number != int(number) or not MIN_REVISION <= number <= MAX_REVISION:
        raise ValueError(f'校准修订号须为 {MIN_REVISION} 至 {MAX_REVISION} 的整数。')
    return int(number)


def _location(value) -> str:
    location = value.strip() if isinstance(value, str) else ''
    if not TOKEN.fullmatch(location):
        raise ValueError('校准的库位须为 1 至 40 位字母、数字、下划线或连字符，首位为字母或数字。')
    return location


def _field(row, *names):
    for name in names:
        if name in row:
            return row[name]
    return None


def _record(row) -> Calibration:
    if isinstance(row, Calibration):
        return row
    if not isinstance(row, dict):
        raise ValueError('校准记录须包含库位、仪表偏差上下限和修订号。')
    location = _location(_field(row, 'location'))
    lower = _number(_field(row, 'bias_lower_pct', 'lower'), '下限')
    upper = _number(_field(row, 'bias_upper_pct', 'upper'), '上限')
    if lower > upper:
        raise ValueError(f'库位 {location} 的仪表偏差下限不可大于上限。')
    return Calibration(location, lower, upper, _revision(_field(row, 'revision', 'rev')))


def _as_records(records) -> list:
    if records is None:
        return []
    if isinstance(records, dict):
        items = []
        for location, value in records.items():
            if isinstance(value, Calibration):
                items.append(value)
            elif isinstance(value, dict):
                items.append({'location': location,
                              'bias_lower_pct': _field(value, 'bias_lower_pct', 'lower'),
                              'bias_upper_pct': _field(value, 'bias_upper_pct', 'upper'),
                              'revision': _field(value, 'revision', 'rev')})
            else:
                raise ValueError(f'库位 {location} 的校准快照格式有误。')
        return items
    return list(records)


def normalize_calibrations(records) -> list[Calibration]:
    """Validate records, rejecting two different claims for the same revision."""
    normalized = []
    seen: dict[tuple[str, int], Calibration] = {}
    for row in _as_records(records):
        item = _record(row)
        key = (item.location, item.revision)
        previous = seen.get(key)
        if previous is not None:
            if previous != item:
                raise ValueError(f'库位 {item.location} 的修订号 {item.revision} 出现内容冲突的校准记录。')
            continue
        seen[key] = item
        normalized.append(item)
    return normalized


def resolve_calibrations(records) -> dict[str, Calibration]:
    """Return the winning record per location: largest revision, arrival order free."""
    winning: dict[str, Calibration] = {}
    for item in normalize_calibrations(records):
        current = winning.get(item.location)
        if current is None or item.revision > current.revision:
            winning[item.location] = item
    return dict(sorted(winning.items()))


def snapshot(records) -> dict[str, dict]:
    """JSON-safe view pinning the winning calibration evidence per location."""
    return {location: {'bias_lower_pct': item.bias_lower_pct, 'bias_upper_pct': item.bias_upper_pct,
                       'revision': item.revision}
            for location, item in resolve_calibrations(records).items()}


def versions(snapshot_map) -> dict[str, int]:
    """Reduce a calibration snapshot to ``location -> revision`` for staleness checks.

    Accepts the mapping form produced by :func:`snapshot` as well as the list
    form embedded in a stored run result (``{"location": …, "revision": …}``).
    """
    if isinstance(snapshot_map, list):
        snapshot_map = {item['location']: item for item in snapshot_map if item.get('location')}
    result = {}
    for location, value in (snapshot_map or {}).items():
        if isinstance(value, Calibration):
            result[location] = value.revision
        elif isinstance(value, dict):
            if value.get('revision') is not None:
                result[location] = int(value['revision'])
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[location] = int(value)
    return dict(sorted(result.items()))


def changed_locations(previous, current) -> list[str]:
    """Locations whose winning revision (or presence) differs between snapshots."""
    before, after = versions(previous), versions(current)
    return sorted(location for location in set(before) | set(after)
                  if before.get(location) != after.get(location))


def as_calibration(value, location: str | None = None) -> Calibration:
    """Coerce a record/dict/snapshot entry into a Calibration."""
    if isinstance(value, Calibration):
        return value
    if isinstance(value, dict) and location is not None:
        value = {**value, 'location': value.get('location', location)}
    return _record(value)


def reading_interval(reading: float, calibration) -> tuple[float, float]:
    """Moisture interval after subtracting the bias bounds from the reading."""
    calibration = as_calibration(calibration)
    return reading - calibration.bias_upper_pct, reading - calibration.bias_lower_pct


def interval_center(reading: float, calibration) -> tuple[float, float]:
    """Return the calibrated midpoint and the half width of the interval."""
    calibration = as_calibration(calibration)
    low, high = reading_interval(reading, calibration)
    return (low + high) / 2, (high - low) / 2

