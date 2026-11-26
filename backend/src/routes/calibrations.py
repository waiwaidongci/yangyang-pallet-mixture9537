from fastapi import APIRouter

from src.controllers.dependencies import Actor, Db, Writer
from src.schemas.calibrations import CalibrationBatch
from src.services import calibrations

router = APIRouter(prefix='/calibrations')


@router.get('')
def state(session: Db, actor: Actor):
    resolved = calibrations.current(session)
    return {'locations': [{'location': location, 'bias_lower_pct': item['bias_lower_pct'],
                           'bias_upper_pct': item['bias_upper_pct'], 'revision': item['revision']}
                          for location, item in resolved.items()],
            'versions': calibrations.current_versions(session)}


@router.get('/records')
def records(session: Db, actor: Actor):
    return calibrations.list_locations(session)


@router.post('', status_code=201)
def submit(payload: CalibrationBatch, session: Db, actor: Writer):
    return calibrations.submit(session, [record.model_dump() for record in payload.records],
                               actor=actor, note=payload.note)
