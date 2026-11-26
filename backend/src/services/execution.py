import json
import logging

from sqlalchemy import select, update

from src.config.database import Session
from src.constants.workflow import RunStatus
from src.domain.calibration import changed_locations, versions
from src.domain.project import build_result, compute, resume as resume_slices
from src.models.calibration import RunState
from src.models.dataset import Dataset
from src.models.run import Run
from src.services import calibrations
from src.services.audit import record


def recover_jobs():
    with Session.begin() as session:
        session.execute(update(Run).where(Run.status == RunStatus.RUNNING).values(status=RunStatus.QUEUED))


def claim():
    with Session.begin() as session:
        run = session.scalar(select(Run).where(Run.status == RunStatus.QUEUED)
                             .order_by(Run.id).limit(1).with_for_update(skip_locked=True))
        if run is None:
            return None
        run.status = RunStatus.RUNNING
        dataset = session.get(Dataset, run.dataset_id)
        record(session, 'run.started', run.id)
        return run.id, dataset.rows, run.settings, run.parent_run_id


def replay(session, parent: Run, current: dict):
    """Rescore only the locations whose winning calibration revision changed.

    The parent run pinned the calibration it was computed with, so the delta is
    ``parents → now`` and every other location reuses its frozen slice.
    """
    stored = session.get(RunState, parent.id)
    if stored is None or not parent.result:
        return None
    before = (parent.result.get('calibration') or {}).get('locations') or []
    targets = changed_locations(before, current)
    scored, frozen = resume_slices(stored.state, current, targets)
    return build_result(frozen, scored, current, recomputed=targets), frozen


def execute(job):
    run_id, rows, settings, parent_run_id = job
    result = None
    state = None
    error_message = None
    try:
        with Session() as session:
            current = calibrations.current(session)
            parent = session.get(Run, parent_run_id) if parent_run_id is not None else None
            replayed = replay(session, parent, current) if parent is not None else None
        if replayed is not None:
            result, state = replayed
        else:
            result, state = compute(rows, settings, current)
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except ValueError as error:
        result = None
        error_message = str(error)
    except Exception:
        logging.getLogger(__name__).exception('计算运行 %s 失败', run_id)
        result = None
        error_message = '计算遇到内部错误，请检查台账后重试。'
    with Session.begin() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.FAILED if error_message is not None else RunStatus.COMPLETED
        run.result = result
        run.error = error_message
        if result is not None:
            # Pin the calibration actually used, so a later correction is
            # detected as a change instead of being mistaken for this input.
            run.calibration_versions = versions(result.get('calibration', {}).get('locations') or [])
        if state is not None:
            stored = session.get(RunState, run_id)
            if stored is None:
                session.add(RunState(run_id=run_id, state=state))
            else:
                stored.state = state
        record(session, 'run.' + run.status, run.id)
