"""HTTP adapter for integrated application operations."""
from flask import Blueprint
from routes.app_commands import invoke, AppCommand, ReconciliationCommand
from services import reconciliation

apps_bp = Blueprint("apps_api", __name__, url_prefix="/api/apps")


@apps_bp.post('/recon/verify')
def recon_verify():
    return invoke(reconciliation.recon_verify, ReconciliationCommand)


@apps_bp.post('/recon/writeback')
def recon_writeback():
    return invoke(reconciliation.recon_writeback, AppCommand)


@apps_bp.post('/recon/audit')
def recon_audit():
    return invoke(reconciliation.recon_audit, AppCommand)
