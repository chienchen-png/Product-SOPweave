"""model_providers / models CRUD for Phase 6."""

from __future__ import annotations

import json
import uuid
from sqlite3 import Connection

from storage._utils import iso_now, row_to_dict

PROVIDER_KINDS = {
    "deepseek",
    "zhipu",
    "siliconflow",
    "dashscope",
    "openai_compatible",
}
API_MODES = {"chat_completions"}
PROVIDER_STATUSES = {"available", "unverified", "disabled", "unknown"}
MODEL_STATUSES = {"available", "unverified", "disabled", "unknown"}

_CAPABILITY_ALIASES = {
    "对话": "chat",
    "chat": "chat",
    "工具": "tools",
    "tool": "tools",
    "tools": "tools",
    "视觉": "vision",
    "vision": "vision",
    "知识库嵌入": "embeddings",
    "embedding": "embeddings",
    "embeddings": "embeddings",
    "知识库排序": "rerank",
    "reranker": "rerank",
    "rerank": "rerank",
}


def _json_object(value: object, *, field: str) -> dict:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} 必须是 JSON 对象") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field} 必须是 JSON 对象")


def _capability_flags(legacy: object) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for item in str(legacy or "").replace("，", ",").split(","):
        normalized = _CAPABILITY_ALIASES.get(item.strip().lower())
        if normalized:
            flags[normalized] = True
    return flags


def _json_text(value: object, *, field: str) -> str:
    return json.dumps(_json_object(value, field=field), ensure_ascii=False, sort_keys=True)


class ModelInUseError(ValueError):
    """An unfinished run still depends on this configuration."""


class ModelConfigurationChangedError(ValueError):
    """A probe result belongs to a previous connection configuration."""


class ModelStore:
    def __init__(self, db: Connection) -> None:
        self.db = db

    def _lock_configuration(self, *, model_id: str = "", provider_id: str = "") -> None:
        # Serialize the check with run creation and retain the write lock until
        # the caller commits its configuration change.
        started = not self.db.in_transaction
        if started:
            self.db.execute("BEGIN IMMEDIATE")
        active = self.db.execute(
            """SELECT 1 FROM runs r JOIN models m ON m.id = r.model_id
               WHERE r.status IN ('pending','starting','running','waiting','resuming')
                 AND (m.id = ? OR m.provider_id = ?) LIMIT 1""",
            (model_id, provider_id),
        ).fetchone()
        if active:
            if started:
                self.db.rollback()
            raise ModelInUseError("该模型或供应商仍有运行中或等待处理的任务，请结束任务后再修改或删除")

    @staticmethod
    def _provider_values(data: dict) -> tuple[str, str, str]:
        provider_kind = str(data.get("provider_kind") or "openai_compatible").strip()
        api_mode = str(data.get("api_mode") or "chat_completions").strip()
        if provider_kind not in PROVIDER_KINDS:
            raise ValueError(f"不支持的供应商类型：{provider_kind}")
        if api_mode not in API_MODES:
            raise ValueError(f"不支持的 API 模式：{api_mode}")
        return (
            provider_kind,
            api_mode,
            _json_text(data.get("options_json"), field="options_json"),
        )

    @staticmethod
    def _normalize_provider(row: dict) -> dict:
        if not row:
            return row
        row["options_json"] = _json_object(row.get("options_json"), field="options_json")
        return row

    @staticmethod
    def _normalize_model(row: dict) -> dict:
        if not row:
            return row
        row["api_model"] = str(row.get("api_model") or row.get("name") or "")
        flags = _json_object(row.get("capabilities_json"), field="capabilities_json")
        row["capabilities_json"] = flags or _capability_flags(row.get("capabilities"))
        row["request_options_json"] = _json_object(
            row.get("request_options_json"), field="request_options_json"
        )
        return row

    # 阶段 7.9.2：模型名称 → 内置官方 logo 自动匹配（无 icon 时回退）
    _ICON_MAP: list[tuple[str, str]] = [
        ("deepseek", "deepseek.png"),
        ("glm", "glm.png"),
        ("qwen", "qwen.png"),
        ("claude", "claude.png"),
        ("gemini", "gemini.png"),
        ("gpt", "openai.png"),
        ("openai", "openai.png"),
    ]

    @staticmethod
    def resolve_model_icon(name: str) -> str:
        """按名称关键字匹配内置官方 logo，返回文件名；未匹配返回 generic.svg。"""
        lower = (name or "").lower()
        for key, file in ModelStore._ICON_MAP:
            if key in lower:
                return file
        return "generic.svg"

    def list_providers(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM model_providers ORDER BY is_preset DESC, name"
        ).fetchall()
        providers = [self._normalize_provider(row_to_dict(row)) for row in rows]
        for provider in providers:
            provider["models"] = self.list_models(provider["id"])

        # Custom sort: 天箭 → Deepseek → Chat Api → others alphabetically
        _order = {"天箭惯性本地大模型": 0, "deepseek": 1, "chat api": 2}
        providers.sort(key=lambda p: (_order.get(p["name"].strip().lower(), 99), p["name"].lower()))

        return providers

    def get_provider(self, provider_id: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM model_providers WHERE id = ?", (provider_id,)
        ).fetchone()
        provider = self._normalize_provider(row_to_dict(row))
        if provider:
            provider["models"] = self.list_models(provider_id)
        return provider

    def create_provider(self, data: dict) -> dict:
        with self.db:
            provider_id = self._insert_provider(data)
        return self.get_provider(provider_id)

    def _insert_provider(self, data: dict) -> str:
        now = iso_now()
        provider_id = str(data.get("id") or uuid.uuid4().hex)
        provider_icon = data.get("icon") or ModelStore.resolve_model_icon(data["name"])
        provider_kind, api_mode, options_json = self._provider_values(data)
        status = str(data.get("status") or "available")
        if status not in PROVIDER_STATUSES:
            raise ValueError(f"不支持的供应商状态：{status}")
        self.db.execute(
            """INSERT INTO model_providers
               (id, name, api_url, api_key, api_key_url, description,
                provider_kind, api_mode, options_json,
                is_preset, status, created_at, updated_at, icon)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
            (
                provider_id, data["name"], data["api_url"], data.get("api_key") or "",
                data.get("api_key_url") or "", data.get("description") or "",
                provider_kind, api_mode, options_json,
                status, now, now, provider_icon,
            ),
        )
        return provider_id

    @staticmethod
    def public_provider(provider: dict) -> dict:
        public = {key: value for key, value in provider.items() if key != "api_key"}
        public["has_api_key"] = bool(provider.get("api_key"))
        if isinstance(public.get("models"), list):
            public["models"] = [
                {key: value for key, value in model.items() if key != "api_key"}
                for model in public["models"]
            ]
        return public

    def update_provider_secret(
        self,
        provider_id: str,
        api_key: str | None,
        clear_api_key: bool,
    ) -> dict | None:
        if clear_api_key and api_key not in (None, ""):
            raise ValueError("不能同时替换并清除 API Key")
        existing = self.get_provider(provider_id)
        if not existing:
            return None
        if not clear_api_key and api_key in (None, ""):
            return existing
        value = "" if clear_api_key else str(api_key)
        now = iso_now()
        with self.db:
            self._lock_configuration(provider_id=provider_id)
            self.db.execute(
                "UPDATE model_providers SET api_key = ?, updated_at = ? WHERE id = ?",
                (value, now, provider_id),
            )
            self.db.execute(
                "UPDATE models SET api_key = ?, updated_at = ? WHERE provider_id = ?",
                (value, now, provider_id),
            )
        return self.get_provider(provider_id)

    def create_provider_bundle(
        self, provider: dict, model: dict
    ) -> tuple[dict, dict]:
        provider_status = str(provider.get("status") or "unverified")
        model_status = str(model.get("status") or "unverified")
        if provider_status not in {"available", "unverified"}:
            raise ValueError(f"不支持的供应商状态：{provider_status}")
        if model_status not in {"available", "unverified"}:
            raise ValueError(f"不支持的模型状态：{model_status}")

        with self.db:
            provider_id = self._insert_provider(
                {**provider, "status": provider_status}
            )
            stored_provider = self.get_provider(provider_id)
            model_id = self._insert_model(
                stored_provider, {**model, "status": model_status}
            )
            self.repair_project_model_references(commit=False)
        return self.get_provider(provider_id), self.get_model(model_id)

    def update_provider(self, provider_id: str, data: dict) -> dict | None:
        existing = self.get_provider(provider_id)
        if not existing:
            return None
        now = iso_now()
        # 图标：显式传入用传入值；未传则按新名称自动匹配（保留旧值优先）
        provider_icon = data.get("icon") or ModelStore.resolve_model_icon(data["name"])
        provider_kind, api_mode, options_json = self._provider_values({**existing, **data})
        api_key = existing.get("api_key") or ""
        if data.get("api_key") not in (None, ""):
            api_key = str(data["api_key"])
        if data.get("clear_api_key") is True:
            if data.get("api_key") not in (None, ""):
                raise ValueError("不能同时替换并清除 API Key")
            api_key = ""
        status = str(data.get("status") or existing.get("status") or "unknown")
        if status not in PROVIDER_STATUSES:
            raise ValueError(f"不支持的供应商状态：{status}")
        connection_changed = (
            str(data['api_url']).rstrip('/') != str(existing.get('api_url') or '').rstrip('/')
            or api_key != existing.get('api_key')
            or provider_kind != existing.get('provider_kind')
            or api_mode != existing.get('api_mode')
            or _json_object(options_json, field='options_json').get('thinking_style')
            != (existing.get('options_json') or {}).get('thinking_style')
        )
        self._lock_configuration(provider_id=provider_id)
        self.db.execute(
            """UPDATE model_providers
               SET name = ?, api_url = ?, api_key = ?, api_key_url = ?,
                   description = ?, provider_kind = ?, api_mode = ?, options_json = ?,
                   status = ?, updated_at = ?, icon = ?
               WHERE id = ?""",
            (
                data["name"], data["api_url"], api_key,
                data.get("api_key_url") or "", data.get("description") or "",
                provider_kind, api_mode, options_json,
                status, now, provider_icon, provider_id,
            ),
        )
        self.db.execute(
            """UPDATE models
               SET api_url = ?, api_key = ?, updated_at = ?
               WHERE provider_id = ?""",
            (data["api_url"], api_key, now, provider_id),
        )
        if connection_changed:
            self._invalidate_provider_models(provider_id, now)
        else:
            self.repair_project_model_references(commit=False)
        self.db.commit()
        return self.get_provider(provider_id)

    def list_models(self, provider_id: str | None = None) -> list[dict]:
        if provider_id:
            rows = self.db.execute(
                "SELECT * FROM models WHERE provider_id = ? ORDER BY name", (provider_id,)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM models ORDER BY is_default DESC, name"
            ).fetchall()
        return [self._normalize_model(row_to_dict(row)) for row in rows]

    def get_model(self, model_id: str) -> dict:
        row = self.db.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
        return self._normalize_model(row_to_dict(row))

    def list_available_models(self, *, chat_only: bool = False) -> list[dict]:
        """Return models that are callable under an available provider.

        Provider-less legacy rows remain supported. Models whose provider was
        disabled or removed are not callable even when their own status says
        ``available``.
        """
        clauses = ["m.status = 'available'", "(m.provider_id IS NULL OR (p.id IS NOT NULL AND p.status = 'available'))"]
        if chat_only:
            clauses.append("m.type = 'chat'")
        rows = self.db.execute(
            f"""SELECT m.* FROM models AS m
                LEFT JOIN model_providers AS p ON p.id = m.provider_id
                WHERE {' AND '.join(clauses)}
                ORDER BY m.is_default DESC, m.name, m.id"""
        ).fetchall()
        return [self._normalize_model(row_to_dict(row)) for row in rows]

    def get_available_model(self, model_ref: str) -> dict:
        """Resolve an available model by stable id, then by legacy name."""
        ref = str(model_ref or "").strip()
        models = self.list_available_models(chat_only=True)
        if not ref:
            return models[0] if models else {}
        return next((m for m in models if m.get("id") == ref), None) or next(
            (m for m in models if m.get("name") == ref), {}
        )

    def resolve_runtime_model(self, model_ref: str) -> dict:
        """Resolve a callable model and its provider into one runtime record."""
        model = self.get_available_model(model_ref)
        if not model:
            raise ValueError(f"所选模型不可用或已删除：{model_ref}")
        provider = self.get_provider(str(model.get("provider_id") or ""))
        if model.get("provider_id") and not provider:
            raise ValueError(f"模型供应商不可用或已删除：{model_ref}")
        provider = provider or {
            "provider_kind": "openai_compatible",
            "api_mode": "chat_completions",
            "options_json": {},
            "api_url": model.get("api_url") or "",
            "api_key": model.get("api_key") or "",
        }
        return {
            **model,
            "provider_name": provider.get("name") or "自定义供应商",
            "provider_kind": provider.get("provider_kind") or "openai_compatible",
            "api_mode": provider.get("api_mode") or "chat_completions",
            "provider_options_json": provider.get("options_json") or {},
            "api_url": provider.get("api_url") or model.get("api_url") or "",
            "api_key": provider.get("api_key") or model.get("api_key") or "",
        }

    def resolve_configured_model(self, model_id: str) -> dict:
        """Resolve a stored model for verification, regardless of availability."""
        model = self.get_model(model_id)
        if not model:
            raise ValueError(f"模型不存在：{model_id}")
        provider = self.get_provider(str(model.get("provider_id") or ""))
        if model.get("provider_id") and not provider:
            raise ValueError(f"模型供应商不存在：{model_id}")
        provider = provider or {
            "provider_kind": "openai_compatible",
            "api_mode": "chat_completions",
            "options_json": {},
            "api_url": model.get("api_url") or "",
            "api_key": model.get("api_key") or "",
        }
        return {
            **model,
            "provider_name": provider.get("name") or "自定义供应商",
            "provider_kind": provider.get("provider_kind") or "openai_compatible",
            "api_mode": provider.get("api_mode") or "chat_completions",
            "provider_options_json": provider.get("options_json") or {},
            "api_url": provider.get("api_url") or model.get("api_url") or "",
            "api_key": provider.get("api_key") or model.get("api_key") or "",
        }

    def invalidate_provider(self, provider_id: str) -> dict | None:
        """Require every model to pass the contract again after connection changes."""
        if not self.get_provider(provider_id):
            return None
        with self.db:
            self._lock_configuration(provider_id=provider_id)
            self._invalidate_provider_models(provider_id, iso_now())
        return self.get_provider(provider_id)

    def _invalidate_provider_models(self, provider_id: str, now: str) -> None:
        self.db.execute(
            "UPDATE model_providers SET status = 'unverified', updated_at = ? WHERE id = ?",
            (now, provider_id),
        )
        self.db.execute(
            "UPDATE models SET status = 'unverified', is_default = 0, updated_at = ? WHERE provider_id = ?",
            (now, provider_id),
        )
        self.repair_project_model_references(commit=False)

    def ensure_model_verification_allowed(self, model_id: str) -> None:
        """Check before probing, releasing the write lock before network I/O."""
        with self.db:
            self._lock_configuration(model_id=model_id)

    def apply_model_verification(
        self, model_id: str, passed: bool, *, expected_record: dict | None = None
    ) -> dict | None:
        """Persist a contract result and derive provider availability from its models."""
        model = self.get_model(model_id)
        if not model:
            return None
        provider_id = str(model.get("provider_id") or "")
        status = "available" if passed else "unverified"
        now = iso_now()
        with self.db:
            self._lock_configuration(model_id=model_id)
            if expected_record is not None:
                current = self.resolve_configured_model(model_id)
                fields = (
                    'api_url', 'api_key', 'api_model', 'provider_kind', 'api_mode',
                    'request_options_json', 'provider_options_json',
                )
                if any(current.get(key) != expected_record.get(key) for key in fields):
                    raise ModelConfigurationChangedError('模型连接配置在检测期间已变更，请重新验证')
            self.db.execute(
                "UPDATE models SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, model_id),
            )
            if provider_id:
                available = self.db.execute(
                    "SELECT 1 FROM models WHERE provider_id = ? AND status = 'available' LIMIT 1",
                    (provider_id,),
                ).fetchone()
                provider_status = "available" if available else "unverified"
                self.db.execute(
                    "UPDATE model_providers SET status = ?, updated_at = ? WHERE id = ?",
                    (provider_status, now, provider_id),
                )
            self.repair_project_model_references(commit=False)
        return self.get_model(model_id)

    def save_capability_checks(self, model_id: str, checks: dict) -> None:
        now = iso_now()
        for name, check in checks.items():
            status = str(check.get("status") or "failed")
            detail = {
                "detail": check.get("detail") or {},
                "error_code": check.get("error_code"),
            }
            self.db.execute(
                """INSERT INTO model_capability_checks
                   (model_id, check_name, status, detail_json, checked_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(model_id, check_name) DO UPDATE SET
                     status=excluded.status,
                     detail_json=excluded.detail_json,
                     checked_at=excluded.checked_at""",
                (model_id, name, status, json.dumps(detail, ensure_ascii=False), now),
            )
        self.db.commit()

    def list_capability_checks(self, model_id: str) -> list[dict]:
        rows = self.db.execute(
            """SELECT model_id, check_name, status, detail_json, checked_at
               FROM model_capability_checks WHERE model_id=? ORDER BY check_name""",
            (model_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = row_to_dict(row)
            item["detail"] = json.loads(item.pop("detail_json") or "{}")
            result.append(item)
        return result

    def _replacement_model_id(self, excluded_ids: set[str] | None = None) -> str | None:
        excluded = excluded_ids or set()
        candidates = [
            model for model in self.list_available_models(chat_only=True)
            if model.get("id") not in excluded
        ]
        if not candidates:
            return None
        default = next((model for model in candidates if model.get("is_default")), None)
        return str((default or candidates[0])["id"])

    def repair_project_model_references(self, *, commit: bool = True) -> int:
        """Normalize legacy names; an unavailable explicit choice requires reselection."""
        models = self.list_available_models(chat_only=True)
        by_id = {str(model["id"]): model for model in models}
        by_name = {str(model["name"]): model for model in models}
        replacement_id = self._replacement_model_id()
        available_defaults = [model for model in models if model.get("is_default")]
        if replacement_id and len(available_defaults) != 1:
            self._set_single_default(replacement_id)
        elif not replacement_id:
            self.db.execute("UPDATE models SET is_default = 0 WHERE is_default <> 0")
        repaired = 0
        rows = self.db.execute(
            "SELECT id, default_model FROM projects WHERE default_model IS NOT NULL AND default_model <> ''"
        ).fetchall()
        for row in rows:
            current = str(row["default_model"])
            if current in by_id:
                continue
            normalized = by_name.get(current)
            if not normalized:
                continue
            next_id = str(normalized["id"])
            self.db.execute(
                "UPDATE projects SET default_model = ?, updated_at = ? WHERE id = ?",
                (next_id, iso_now(), row["id"]),
            )
            repaired += 1
        if commit:
            self.db.commit()
        return repaired

    def create_model(self, provider_id: str, data: dict) -> dict | None:
        provider = self.get_provider(provider_id)
        if not provider:
            return None
        with self.db:
            model_id = self._insert_model(provider, data)
            if data.get("is_default"):
                self._set_single_default(model_id)
            self.repair_project_model_references(commit=False)
        return self.get_model(model_id)

    def _insert_model(self, provider: dict, data: dict) -> str:
        now = iso_now()
        provider_id = str(provider["id"])
        model_id = str(data.get("id") or uuid.uuid4().hex)
        model_icon = data.get("icon") or ModelStore.resolve_model_icon(data["name"])
        api_model = str(data.get("api_model") or data["name"]).strip()
        if not api_model:
            raise ValueError("API 模型标识不能为空")
        status = str(data.get("status") or "available")
        if status not in MODEL_STATUSES:
            raise ValueError(f"不支持的模型状态：{status}")
        capability_flags = data.get("capabilities_json")
        if capability_flags in (None, ""):
            capability_flags = _capability_flags(data.get("capabilities"))
        capabilities_json = _json_text(capability_flags, field="capabilities_json")
        request_options_json = _json_text(
            data.get("request_options_json"), field="request_options_json"
        )
        self.db.execute(
            """INSERT INTO models
               (id, provider_id, name, api_url, api_key, type, capabilities,
                api_model, capabilities_json, request_options_json,
                context_window, is_default, status, created_at, updated_at, icon)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                model_id, provider_id, data["name"], provider["api_url"],
                provider.get("api_key") or "", data.get("type") or "chat",
                data.get("capabilities") or "", api_model, capabilities_json,
                request_options_json,
                data.get("context_window") or 0, 1 if data.get("is_default") else 0,
                status, now, now, model_icon,
            ),
        )
        return model_id

    def update_model(self, model_id: str, data: dict) -> dict | None:
        existing = self.get_model(model_id)
        if not existing:
            return None
        now = iso_now()
        # 图标：显式传入用传入值；未传则按新名称自动匹配
        model_icon = data.get("icon") or ModelStore.resolve_model_icon(data["name"])
        api_model = str(data.get("api_model") or existing.get("api_model") or existing["name"]).strip()
        capability_source = data.get("capabilities_json")
        if capability_source in (None, ""):
            capability_source = _capability_flags(data.get("capabilities")) \
                if "capabilities" in data else existing.get("capabilities_json")
        capabilities_json = _json_text(capability_source, field="capabilities_json")
        request_options_json = _json_text(
            data.get("request_options_json", existing.get("request_options_json")),
            field="request_options_json",
        )
        status = str(data.get("status") or existing.get("status") or "unknown")
        if status not in MODEL_STATUSES:
            raise ValueError(f"不支持的模型状态：{status}")
        self._lock_configuration(model_id=model_id)
        self.db.execute(
            """UPDATE models
               SET name = ?, type = ?, capabilities = ?, context_window = ?,
                   api_model = ?, capabilities_json = ?, request_options_json = ?,
                   is_default = ?, status = ?, updated_at = ?, icon = ?
               WHERE id = ?""",
            (
                data["name"], data.get("type") or "chat", data.get("capabilities") or "",
                data.get("context_window") or 0, api_model, capabilities_json,
                request_options_json, 1 if data.get("is_default") else 0,
                status, now, model_icon, model_id,
            ),
        )
        if data.get("is_default"):
            self._set_single_default(model_id)
        self.repair_project_model_references(commit=False)
        self.db.commit()
        return self.get_model(model_id)

    def set_default_model(self, model_id: str) -> dict | None:
        model = self.get_available_model(model_id)
        if not model or model.get("id") != model_id or model.get("type") != "chat":
            return None
        self._set_single_default(model_id)
        self.db.commit()
        return self.get_model(model_id)

    def delete_model(self, model_id: str) -> bool:
        existing = self.get_model(model_id)
        if not existing:
            return False
        self._lock_configuration(model_id=model_id)
        replacement_id = self._replacement_model_id({model_id})
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO deleted_presets (id, deleted_at) VALUES (?, ?)",
                (model_id, iso_now()),
            )
            self.db.execute("DELETE FROM models WHERE id = ?", (model_id,))
            if replacement_id:
                self._set_single_default(replacement_id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return True

    def delete_provider(self, provider_id: str) -> bool:
        existing = self.get_provider(provider_id)
        if not existing:
            return False
        self._lock_configuration(provider_id=provider_id)
        models = existing.get("models") or []
        removed_ids = {str(model["id"]) for model in models}
        replacement_id = self._replacement_model_id(removed_ids)
        now = iso_now()
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO deleted_presets (id, deleted_at) VALUES (?, ?)",
                (provider_id, now),
            )
            for model_id in removed_ids:
                self.db.execute(
                    "INSERT OR IGNORE INTO deleted_presets (id, deleted_at) VALUES (?, ?)",
                    (model_id, now),
                )
            self.db.execute("DELETE FROM models WHERE provider_id = ?", (provider_id,))
            self.db.execute("DELETE FROM model_providers WHERE id = ?", (provider_id,))
            if replacement_id:
                self._set_single_default(replacement_id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return True

    def _set_single_default(self, model_id: str) -> None:
        self.db.execute("UPDATE models SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END", (model_id,))
