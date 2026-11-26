from fastapi import APIRouter, Query

from src.controllers.dependencies import Actor, Admin, Db, Writer
from src.controllers.report import current_detail, current_report_csv, report_csv, report_detail
from src.schemas.reviews import ReviewCreate, ReviewInfo
from src.schemas.runs import RunCreate, RunInfo
from src.services import reviews, runs

router = APIRouter(prefix='/runs')


@router.get('', response_model=list[RunInfo])
def list_all(session: Db, actor: Actor):
    return runs.list_runs(session, actor)


@router.post('', response_model=RunInfo, status_code=202)
def create(payload: RunCreate, session: Db, actor: Writer):
    return runs.create_run(session, payload, actor)


@router.get('/current')
def current(session: Db, actor: Actor, dataset_id: int | None = Query(default=None)):
    return current_detail(session, dataset_id, actor)


@router.get('/current/export/{key}')
def current_export(key: str, session: Db, actor: Actor, dataset_id: int | None = Query(default=None)):
    return current_report_csv(session, key, actor, dataset_id)


@router.get('/{run_id}')
def detail(run_id: int, session: Db, actor: Actor):
    return report_detail(session, run_id, actor)


@router.get('/{run_id}/pending-changes')
def pending_changes(run_id: int, session: Db, actor: Actor):
    run, _review = runs.get_run(session, run_id, actor)
    return {'locations': runs.pending_changes(session, run)}


@router.post('/{run_id}/review', response_model=ReviewInfo, status_code=201)
def review(run_id: int, payload: ReviewCreate, session: Db, actor: Admin):
    return reviews.submit(session, run_id, payload, actor)


@router.get('/{run_id}/export/{key}')
def export(run_id: int, key: str, session: Db, actor: Actor):
    return report_csv(session, run_id, key, actor)
