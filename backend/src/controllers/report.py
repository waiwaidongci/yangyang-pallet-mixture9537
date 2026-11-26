from fastapi.responses import Response

from src.services import runs
from src.services.reviews import review_info
from src.utils.csv_files import write_rows


def detail_shape(run, review):
    return {**runs.run_info(run, review, run.id), 'result': run.result, 'error': run.error,
            'review': review_info(review) if review else None}


def report_detail(session, run_id, actor):
    return detail_shape(*runs.get_run(session, run_id, actor))


def current_detail(session, dataset_id, actor):
    return detail_shape(*runs.current_release(session, dataset_id))


def _csv(run, table, prefix):
    return Response(write_rows(table['columns'], table['rows']), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{prefix}-{run.id}-{table["key"]}.csv"'})


def report_csv(session, run_id, key, actor):
    table = runs.export_table(session, run_id, key, actor)
    return _csv(runs.get_run(session, run_id, actor)[0], table, 'run')


def current_report_csv(session, key, actor, dataset_id=None):
    table, run = runs.export_current_table(session, key, dataset_id, actor)
    return _csv(run, table, 'handoff')
