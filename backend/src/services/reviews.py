from fastapi import HTTPException
from sqlalchemy import select

from src.constants.workflow import Decision, RunStatus
from src.models.calibration import DatasetRelease
from src.models.review import Review
from src.models.run import Run
from src.services.audit import record
from src.services.runs import assert_current
from src.utils.time import utc_stamp


def review_info(review):
    return {'run_id': review.run_id, 'decision': review.decision, 'note': review.note, 'created_at': review.created_at}


def submit(session, run_id, payload, actor):
    run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run:
        raise LookupError('找不到运行')
    if run.status != RunStatus.COMPLETED or session.get(Review, run_id):
        raise HTTPException(409, '仅可复核一次，且计算必须成功完成')
    assert_current(session, run)
    review = Review(run_id=run_id, reviewer_id=actor.id, decision=payload.decision,
                    note=payload.note, created_at=utc_stamp())
    session.add(review)
    session.flush()
    switched = payload.decision == Decision.APPROVED and switch_release(session, run, actor)
    record(session, 'review.' + payload.decision, run_id, actor.id)
    session.commit()
    return {**review_info(review), 'switched': switched}


def _release_versions(run: Run) -> dict[str, int]:
    locations = ((run.result or {}).get('calibration') or {}).get('locations') or []
    return {item['location']: item['revision'] for item in locations}


def switch_release(session, run: Run, actor) -> bool:
    """Move the stable handoff forward inside the approval transaction.

    Only a newer run may replace the live handoff, so an older approval that
    completes late never overwrites a newer switch.  The previous run keeps its
    own immutable URL and stays readable either way.
    """
    release = session.get(DatasetRelease, run.dataset_id, with_for_update=True)
    if release is not None and release.run_id > run.id:
        return False
    stamp = utc_stamp()
    revisions = _release_versions(run)
    if release is None:
        session.add(DatasetRelease(dataset_id=run.dataset_id, run_id=run.id,
                                   revisions=revisions, switched_at=stamp))
    else:
        release.run_id = run.id
        release.revisions = revisions
        release.switched_at = stamp
    record(session, 'release.switched', run.id, actor.id)
    return True
