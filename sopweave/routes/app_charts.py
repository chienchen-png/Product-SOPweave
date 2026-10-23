"""HTTP adapter for integrated application operations."""
from flask import Blueprint
from routes.app_commands import invoke, AppCommand
from services import finmod_charts

app_charts_bp = Blueprint("app_charts", __name__, url_prefix="")


@app_charts_bp.post('/charts-suggest')
def finmod_charts_suggest():
    return invoke(finmod_charts.finmod_charts_suggest, AppCommand)


@app_charts_bp.post('/py-chart')
def finmod_py_chart():
    return invoke(finmod_charts.finmod_py_chart, AppCommand)
