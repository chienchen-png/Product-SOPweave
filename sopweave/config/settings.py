from pathlib import Path
import os

from config.brand import APP_NAME, LEGACY_ENV_PREFIX, ENV_PREFIX


def _setting(name: str, default: str, *, legacy: str = "") -> str:
    """Prefer the SOPweave variable while accepting the old alias once."""
    value = os.environ.get(f"{ENV_PREFIX}{name}")
    if value is not None:
        return value
    if legacy:
        value = os.environ.get(legacy)
        if value is not None:
            return value
    return default


HOST = "127.0.0.1"
PORT = 8080
DEBUG = _setting("DEBUG", "0", legacy=f"{LEGACY_ENV_PREFIX}DEBUG") == "1"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = (
    BASE_DIR / _setting("DATA_DIR", "data", legacy=f"{LEGACY_ENV_PREFIX}DATA_DIR")
).resolve()
SNAPSHOT_RETENTION_DAYS = max(
    0,
    int(
        _setting(
            "SNAPSHOT_RETENTION_DAYS",
            "0",
            legacy=f"{LEGACY_ENV_PREFIX}SNAPSHOT_RETENTION_DAYS",
        )
    ),
)
DB_PATH = DATA_DIR / "app.db"
USER_SKILLS_ENABLED = (
    _setting(
        "USER_SKILLS_ENABLED",
        "1",
        legacy=f"{LEGACY_ENV_PREFIX}USER_SKILLS_ENABLED",
    )
    == "1"
)
AGENT_EXECUTOR = _setting(
    "EXECUTOR",
    "local",
    legacy=f"{LEGACY_ENV_PREFIX}AGENT_EXECUTOR",
).strip() or "local"
AGENT_WORKER_IMAGE = _setting(
    "EXECUTOR_IMAGE",
    "",
    legacy=f"{LEGACY_ENV_PREFIX}AGENT_WORKER_IMAGE",
).strip()
IMPORT_MAX_CELLS = max(
    0,
    int(
        _setting(
            "IMPORT_MAX_CELLS",
            "2000000",
            legacy=f"{LEGACY_ENV_PREFIX}IMPORT_MAX_CELLS",
        )
    ),
)
ANALYSIS_MAX_CELLS = max(
    0,
    int(
        _setting(
            "ANALYSIS_MAX_CELLS",
            "2000000",
            legacy=f"{LEGACY_ENV_PREFIX}ANALYSIS_MAX_CELLS",
        )
    ),
)
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
# 新前端构建产物目录（Vite build 输出，由 Flask 直接托管）
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"
