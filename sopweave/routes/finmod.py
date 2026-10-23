"""HTTP adapter for integrated application operations."""
from flask import Blueprint
from routes.app_commands import invoke, AppCommand, ModelRunCommand
from services import finmod_guidance
from services import finmod_mapping
from services import finmod_compute
from services import finmod_catalog

finmod_bp = Blueprint("finmod_api", __name__, url_prefix="/api/apps/finmod")


@finmod_bp.get('/meta')
def finmod_meta():
    return invoke(finmod_catalog.finmod_meta)


@finmod_bp.get('/models/<model_id>')
def finmod_model_detail(model_id: str):
    return invoke(finmod_catalog.finmod_model_detail, model_id=model_id)


@finmod_bp.get('/charts/<chart_id>')
def finmod_chart_detail(chart_id: str):
    return invoke(finmod_catalog.finmod_chart_detail, chart_id=chart_id)


@finmod_bp.post('/guide')
def finmod_guide():
    return invoke(finmod_guidance.finmod_guide, AppCommand)


@finmod_bp.post('/varmap-suggest')
def finmod_varmap_suggest():
    return invoke(finmod_guidance.finmod_varmap_suggest, AppCommand)


@finmod_bp.post('/varmap')
def finmod_varmap():
    return invoke(finmod_mapping.finmod_varmap, AppCommand)


@finmod_bp.post('/prep-data')
def finmod_prep_data():
    return invoke(finmod_mapping.finmod_prep_data, AppCommand)


@finmod_bp.post('/derived-eval')
def finmod_derived_eval():
    return invoke(finmod_mapping.finmod_derived_eval, AppCommand)


@finmod_bp.post('/run')
def finmod_run():
    return invoke(finmod_compute.finmod_run, ModelRunCommand)


@finmod_bp.post('/analyze')
def finmod_analyze():
    return invoke(finmod_compute.finmod_analyze, AppCommand)


from routes.app_charts import app_charts_bp
from routes.app_reports import app_reports_bp
finmod_bp.register_blueprint(app_charts_bp)
finmod_bp.register_blueprint(app_reports_bp)
