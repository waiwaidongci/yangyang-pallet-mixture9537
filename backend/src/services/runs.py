import math

from fastapi import HTTPException
from sqlalchemy import select

from src.constants.workflow import Decision, Role, RunStatus
from src.domain.calibration import changed_locations
from src.domain.project import SPEC
from src.models.calibration import DatasetRelease
from src.models.review import Review
from src.models.run import Run
from src.services import calibrations
from src.services.audit import record
from src.services.catalog import get_dataset
from src.services.queue import notify_worker
from src.utils.time import utc_stamp


def run_info(run, review, current_run_id: int | None = None):
    fields = ('id', 'label', 'dataset_id', 'created_at', 'status', 'settings', 'parent_run_id')
    decision = review.decision if review else None
    is_current = None if current_run_id is None else run.id == current_run_id
    return {**{key: getattr(run, key) for key in fields}, 'decision': decision,
            'calibration_versions': run.calibration_versions or {},
            'current': is_current, 'is_current': is_current,
            'released': decision == Decision.APPROVED and current_run_id is not None}


def _release_ids(session) -> dict[int, int]:
    return {release.dataset_id: release.run_id for release in session.scalars(select(DatasetRelease))}


def current_release_run_id(session, dataset_id: int) -> int | None:
    release = session.get(DatasetRelease, dataset_id)
    return release.run_id if release is not None else None


def list_runs(session, actor):
    statement = select(Run, Review).outerjoin(Review, Run.id == Review.run_id).order_by(Run.id.desc()).limit(100)
    if actor.role == Role.VIEWER:
        statement = statement.where(Review.decision == Decision.APPROVED)
    releases = _release_ids(session)
    return [run_info(run, review, releases.get(run.dataset_id)) for run, review in session.execute(statement)]


def get_run(session, run_id, actor):
    run = session.get(Run, run_id)
    review = session.get(Review, run_id)
    if run is None or (actor.role == Role.VIEWER and (not review or review.decision != Decision.APPROVED)):
        raise LookupError('找不到可查看的运行')
    return run, review


def validate_settings(settings):
    parameters = {item['key']: item for item in SPEC['settings']}
    if settings.keys() - parameters.keys():
        raise ValueError('存在本项目未定义的运行参数')
    checked = {}
    for key, parameter in parameters.items():
        value = settings.get(key, parameter['default'])
        if not math.isfinite(value) or not parameter['min'] <= value <= parameter['max']:
            raise ValueError(f"{parameter['label']}超出范围")
        if parameter['step'] == 1 and value != int(value):
            raise ValueError(f"{parameter['label']}必须是整数")
        checked[key] = int(value) if parameter['step'] == 1 else value
    return checked


def _latest_dataset_run(session, dataset_id: int) -> Run | None:
    return session.scalar(select(Run).where(Run.dataset_id == dataset_id, Run.status == RunStatus.COMPLETED)
                          .order_by(Run.id.desc()).limit(1))


def create_run(session, payload, actor):
    get_dataset(session, payload.dataset_id)
    parent = None
    if payload.parent_run_id is not None:
        parent = session.get(Run, payload.parent_run_id)
        if parent is None or parent.dataset_id != payload.dataset_id or parent.result is None \
                or parent.status != RunStatus.COMPLETED:
            raise HTTPException(409, '增量重算须引用同一台账中已完成的运行')
    elif payload.recompute:
        parent = _latest_dataset_run(session, payload.dataset_id)
        if parent is None or parent.result is None:
            raise HTTPException(409, '该台账还没有可增量重算的已完成运行')
    settings = validate_settings(payload.settings if payload.settings or parent is None else parent.settings)
    run = Run(label=payload.label, dataset_id=payload.dataset_id, created_by=actor.id,
              created_at=utc_stamp(), status=RunStatus.QUEUED, settings=settings,
              parent_run_id=parent.id if parent is not None else None,
              calibration_versions=calibrations.current_versions(session))
    session.add(run)
    session.flush()
    record(session, 'run.queued', run.id, actor.id)
    session.commit()
    notify_worker()
    return run_info(run, None)


def pending_changes(session, run) -> list[str]:
    """Locations whose winning revision moved since the run pinned its input."""
    return changed_locations(run.calibration_versions or {}, calibrations.current(session))


def assert_current(session, run):
    changed = pending_changes(session, run)
    if changed:
        raise HTTPException(409, '校准记录已在本次计算后更新（库位 ' + '、'.join(changed) +
                            '），请按最新校准重算并复核后再切换分拣单。')


def export_table(session, run_id, key, actor):
    run, review = get_run(session, run_id, actor)
    if review is None or review.decision != Decision.APPROVED:
        raise HTTPException(409, '复核通过后可下载正式 CSV')
    return table_for_key(run, key)


def table_for_key(run: Run, key: str) -> dict:
    if not run.result:
        raise LookupError('找不到结果表')
    for table in run.result['tables']:
        if table['key'] == key:
            return table
    raise LookupError('找不到结果表')


def current_release(session, dataset_id: int | None = None):
    """Return the atomically switched version that the stable download serves."""
    if dataset_id is not None:
        release = session.get(DatasetRelease, dataset_id)
        if release is None:
            raise HTTPException(409, '该台账还没有复核通过并切换的分拣单')
    else:
        release = session.scalar(select(DatasetRelease).order_by(DatasetRelease.switched_at.desc(),
                                                               DatasetRelease.dataset_id.desc()).limit(1))
        if release is None:
            raise HTTPException(409, '还没有复核通过并切换的分拣单')
    return session.get(Run, release.run_id), session.get(Review, release.run_id)


def export_current_table(session, key: str, dataset_id: int | None = None, actor=None):
    run, _review = current_release(session, dataset_id)
    return table_for_key(run, key), run


# Descriptive aliases kept for callers that describe the switched handoff.
get_current_release = current_release
current_handoff = current_release
export_active_table = export_current_table
