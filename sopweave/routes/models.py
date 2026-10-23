from __future__ import annotations
from services.model_usage import recorder_for_connection

import asyncio
import json
import sqlite3
import uuid

from flask import Blueprint

from agent_runtime.models.contract import (
    classify_provider_error,
    contract_runner_for,
)
from agent_runtime.models.factory import ModelRuntimeFactory, resolve_model_config
from agent_runtime.models.adapters import adapter_for
from agent_runtime.models.templates import (
    get_provider_template,
    list_provider_templates,
)
from agent_runtime.models.types import ProviderKind
from agent_runtime.models.types import RuntimePurpose
from routes._utils import error, payload, success
from storage.db import get_db
from storage.log_store import LogStore
from storage.model_store import ModelConfigurationChangedError, ModelInUseError, ModelStore

models_bp = Blueprint("models_api", __name__, url_prefix="/api/models")


@models_bp.errorhandler(ModelInUseError)
def model_in_use(exc):
    return error(str(exc), 409, {"code": "model_in_use"})


@models_bp.errorhandler(ModelConfigurationChangedError)
def model_configuration_changed(exc):
    return error(str(exc), 409, {"code": "model_configuration_changed"})

_PROVIDER_KINDS = {"deepseek", "zhipu", "siliconflow", "openai_compatible"}
_API_MODES = {"chat_completions"}
_THINKING_STYLES = {"none", "thinking_object", "enable_thinking"}


class BundleValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _object_field(data: dict, name: str) -> tuple[dict | None, str | None]:
    value = data.get(name)
    if value in (None, ""):
        return {}, None
    if isinstance(value, dict):
        return value, None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None, f"{name} 必须是 JSON 对象"
        if isinstance(parsed, dict):
            return parsed, None
    return None, f"{name} 必须是 JSON 对象"


def _log(db, event_type: str, summary: str, status: str = "success",
         detail: str | None = None) -> None:
    """Best-effort local_logs write (PRD §3.8 最简化本地日志)."""
    try:
        LogStore(db).write(event_type=event_type, event_summary=summary,
                           status=status, detail=detail)
    except Exception:
        pass


def _provider_payload(data: dict) -> tuple[dict | None, str | None]:
    name = str(data.get("name") or "").strip()
    api_url = str(data.get("api_url") or "").strip()
    if not name:
        return None, "供应商名称不能为空"
    if not api_url:
        return None, "API URL 不能为空"
    provider_kind = str(data.get("provider_kind") or "openai_compatible").strip()
    api_mode = str(data.get("api_mode") or "chat_completions").strip()
    if provider_kind not in _PROVIDER_KINDS:
        return None, "供应商类型无效"
    if api_mode not in _API_MODES:
        return None, "API 模式无效"
    options, options_error = _object_field(data, "options_json")
    if options_error:
        return None, options_error
    result = {
        "name": name,
        "api_url": api_url,
        "api_key_url": str(data.get("api_key_url") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "icon": str(data.get("icon") or "").strip(),
    }
    if str(data.get("api_key") or "").strip():
        result["api_key"] = str(data["api_key"]).strip()
    if data.get("clear_api_key") is True:
        result["clear_api_key"] = True
    if "provider_kind" in data:
        result["provider_kind"] = provider_kind
    if "api_mode" in data:
        result["api_mode"] = api_mode
    if "options_json" in data:
        result["options_json"] = options
    try:
        result["api_url"] = adapter_for(provider_kind).normalize_base_url(api_url).rstrip("/")
    except ValueError as exc:
        return None, str(exc)
    return result, None


def _number(
    data: dict, name: str, default: float, *, integer: bool = False
) -> int | float:
    value = data.get(name, default)
    try:
        parsed = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise BundleValidationError(f"invalid_{name}", f"{name} 必须是数字") from exc
    if parsed < 0 or (name == "timeout" and parsed == 0):
        raise BundleValidationError(f"invalid_{name}", f"{name} 超出允许范围")
    return parsed


def _bundle_records(data: dict) -> tuple[dict, dict, dict]:
    template_id = str(data.get("template_id") or "").strip()
    try:
        template = get_provider_template(template_id)
    except ValueError as exc:
        raise BundleValidationError("unknown_template", str(exc)) from exc

    api_key = str(data.get("api_key") or "").strip()
    api_model = str(data.get("api_model") or "").strip()
    if not api_key:
        raise BundleValidationError("api_key_required", "API Key 不能为空")
    if not api_model:
        raise BundleValidationError("api_model_required", "API 模型标识不能为空")

    if template.is_custom:
        provider_name = str(data.get("provider_name") or "").strip()
        base_url = str(data.get("base_url") or "").strip()
        if not provider_name:
            raise BundleValidationError("provider_name_required", "供应商名称不能为空")
        if not base_url:
            raise BundleValidationError("base_url_required", "Base URL 不能为空")
        api_mode = str(data.get("api_mode") or "").strip()
        if api_mode != "chat_completions":
            raise BundleValidationError(
                "unsupported_api_mode", "仅支持 OpenAI Chat Completions"
            )
        auth_mode = str(data.get("auth_mode") or "").strip()
        if auth_mode != "bearer":
            raise BundleValidationError(
                "unsupported_auth_mode", "仅支持 Bearer API Key 鉴权"
            )
        thinking_style = str(data.get("thinking_style") or "").strip()
        if thinking_style not in _THINKING_STYLES:
            raise BundleValidationError(
                "unsupported_thinking_style", "思考参数兼容方式无效"
            )
        try:
            base_url = adapter_for(ProviderKind.OPENAI_COMPATIBLE).normalize_base_url(
                base_url
            ).rstrip("/")
        except ValueError as exc:
            raise BundleValidationError("invalid_base_url", str(exc)) from exc
    else:
        locked = {
            "provider_name": template.display_name,
            "base_url": template.base_url,
            "api_mode": template.api_mode.value,
            "auth_mode": template.auth_mode,
            "thinking_style": template.thinking_style,
        }
        for field, expected in locked.items():
            if field in data and str(data.get(field) or "").strip() != str(expected):
                raise BundleValidationError(
                    "official_field_locked", f"官方模板字段 {field} 不可修改"
                )
        provider_name = template.display_name
        base_url = str(template.base_url)
        api_mode = template.api_mode.value
        auth_mode = template.auth_mode
        thinking_style = template.thinking_style

    timeout = _number(data, "timeout", 30)
    max_retries = _number(data, "max_retries", 0, integer=True)
    context_window = _number(data, "context_window", 0, integer=True)
    provider_options = {
        "thinking_style": thinking_style,
        "timeout": timeout,
        "max_retries": max_retries,
    }
    provider = {
        "name": provider_name,
        "api_url": base_url,
        "api_key": api_key,
        "provider_kind": template.provider_kind.value,
        "api_mode": api_mode,
        "options_json": provider_options,
        "description": "",
    }
    model = {
        "name": api_model,
        "api_model": api_model,
        "type": "chat",
        "context_window": context_window,
        "request_options_json": {},
    }
    probe = {
        "id": "provider-bundle-check",
        "name": api_model,
        "api_model": api_model,
        "api_url": base_url,
        "api_key": api_key,
        "provider_kind": template.provider_kind.value,
        "api_mode": api_mode,
        "capabilities_json": {"chat": True, "stream": True, "tools": True},
        "request_options_json": {},
        "provider_options_json": provider_options,
    }
    return provider, model, probe


def _public_model(model: dict) -> dict:
    return {key: value for key, value in model.items() if key != "api_key"}


def _model_payload(data: dict) -> tuple[dict | None, str | None]:
    name = str(data.get("name") or "").strip()
    if not name:
        return None, "模型名称不能为空"
    try:
        context_window = int(data.get("context_window") or 0)
    except (TypeError, ValueError):
        return None, "上下文窗口必须是数字"
    capabilities_json, capabilities_error = _object_field(data, "capabilities_json")
    if capabilities_error:
        return None, capabilities_error
    request_options, request_error = _object_field(data, "request_options_json")
    if request_error:
        return None, request_error
    result = {
        "name": name,
        "api_model": str(data.get("api_model") or "").strip() or None,
        "type": str(data.get("type") or "chat").strip() or "chat",
        "capabilities": str(data.get("capabilities") or "").strip(),
        "context_window": max(context_window, 0),
        "is_default": bool(data.get("is_default")),
        "icon": str(data.get("icon") or "").strip(),
    }
    if "capabilities_json" in data:
        result["capabilities_json"] = capabilities_json
    if "request_options_json" in data:
        result["request_options_json"] = request_options
    return result, None


@models_bp.post("/test")
def test_connection():
    """Test a model API connection (PRD §2.5/§3.6 测试连接).

    Sends one short chat completion to the given endpoint and returns
    the model info, so users can validate configuration before saving.
    """
    data = payload()
    mode = str(data.get("mode") or "basic").strip()
    if mode not in {"basic", "agent_contract"}:
        return error("连接测试模式无效")
    if mode == "agent_contract":
        return _test_agent_contract(data)

    api_url = str(data.get("api_url") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    model = str(data.get("model") or "").strip()
    if not api_url or not model:
        return error("API 地址和模型名称必填")

    record = {
        "id": "connection-test",
        "name": model,
        "api_model": model,
        "api_url": api_url,
        "api_key": api_key,
        "provider_kind": str(data.get("provider_kind") or "openai_compatible"),
        "api_mode": str(data.get("api_mode") or "chat_completions"),
        "capabilities_json": {"chat": True},
        "request_options_json": data.get("request_options_json") or {},
        "provider_options_json": {
            **(data.get("options_json") or {}),
            "max_retries": 0,
        },
    }
    try:
        raw = asyncio.run(_run_basic_probe(record))
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:  # noqa: BLE001
        error_code = classify_provider_error(exc)
        _log(get_db(), "model.test_failed", f"测试连接失败: {api_url}",
             status="error", detail=error_code)
        return error(f"连接失败：{error_code}")

    model_name = raw["model"] or model
    usage = raw["usage"]
    return success({
        "message": f"连接成功，模型响应: {model_name}",
        "model": model_name,
        "usage": usage,
    })


async def _run_basic_probe(record: dict) -> dict:
    config = resolve_model_config(record)
    client = ModelRuntimeFactory(usage_recorder=recorder_for_connection(get_db())).build_direct_client(
        config, RuntimePurpose.PROVIDER_CHECK
    )
    try:
        response = await client.complete(
            [{"role": "user", "content": "ping"}], max_tokens=8
        )
        usage = getattr(response, "usage", None)
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        elif not isinstance(usage, dict):
            usage = {}
        return {
            "model": str(getattr(response, "model", "") or config.api_model),
            "usage": usage,
        }
    finally:
        await client.aclose()


def _test_agent_contract(data: dict):
    db = get_db()
    store = ModelStore(db)
    model_id = str(data.get("model_id") or "").strip()
    if model_id:
        store.ensure_model_verification_allowed(model_id)
    try:
        if model_id:
            record = store.resolve_configured_model(model_id)
        else:
            api_url = str(data.get("api_url") or "").strip()
            model = str(data.get("model") or data.get("api_model") or "").strip()
            if not api_url or not model:
                return error("API 地址和模型名称必填")
            record = {
                "id": "connection-test",
                "name": model,
                "api_model": model,
                "api_url": api_url,
                "api_key": str(data.get("api_key") or "").strip(),
                "provider_kind": str(
                    data.get("provider_kind") or "openai_compatible"
                ).strip(),
                "api_mode": str(data.get("api_mode") or "chat_completions").strip(),
                "capabilities_json": {"chat": True, "stream": True, "tools": True},
                "request_options_json": data.get("request_options_json") or {},
                "provider_options_json": {
                    **(data.get("options_json") or {}),
                    "max_retries": 0,
                },
            }
        configured_record = record
        provider_options = dict(record.get("provider_options_json") or {})
        provider_options["max_retries"] = 0
        record = {**record, "provider_options_json": provider_options}
        result = asyncio.run(_run_agent_contract(record))
    except ValueError as exc:
        return error(str(exc))
    except Exception as exc:  # noqa: BLE001 - configuration probe returns a concise failure
        _log(db, "model.contract_failed", "模型兼容性检查无法启动", "error", str(exc)[:200])
        return error(f"兼容性检查无法启动: {type(exc).__name__}")

    output = result.to_dict()
    if model_id:
        saved = store.apply_model_verification(
            model_id, output["passed"], expected_record=configured_record
        )
        if not saved:
            return error("模型已删除，检测结果未保存", 404)
        store.save_capability_checks(model_id, output["checks"])
    _log(
        db,
        "model.contract_checked",
        f"模型兼容性检查完成: {model_id or record['api_model']}",
        "success" if output["passed"] else "error",
    )
    return success(output)


async def _run_agent_contract(record: dict):
    config = resolve_model_config(record)
    client = ModelRuntimeFactory(usage_recorder=recorder_for_connection(get_db())).build_direct_client(
        config, RuntimePurpose.PROVIDER_CHECK
    )
    try:
        return await contract_runner_for(config, client).run()
    finally:
        await client.aclose()


@models_bp.get("/providers")
def list_providers():
    store = ModelStore(get_db())
    providers = []
    for provider in store.list_providers():
        public = store.public_provider(provider)
        for model in public.get("models", []):
            model["capability_checks"] = store.list_capability_checks(model["id"])
        providers.append(public)
    return success(providers)


@models_bp.get("/provider-templates")
def provider_templates():
    return success([template.to_dict() for template in list_provider_templates()])


@models_bp.post("/provider-bundles")
def create_provider_bundle():
    db = get_db()
    store = ModelStore(db)
    try:
        provider_data, model_data, probe = _bundle_records(payload())
        provider_data['id'] = uuid.uuid4().hex
        model_data['id'] = uuid.uuid4().hex
        probe.update(id=model_data['id'], provider_id=provider_data['id'],
                     provider_name=provider_data['name'])
        verification = asyncio.run(_run_agent_contract(probe)).to_dict()
        status = "available" if verification["passed"] else "unverified"
        checks = verification["checks"]
        model_data["capabilities_json"] = {
            name: check.get("status") == "passed" for name, check in checks.items()
        }
        provider, model = store.create_provider_bundle(
            {**provider_data, "status": status},
            {**model_data, "status": status},
        )
        store.save_capability_checks(model["id"], checks)
    except BundleValidationError as exc:
        return error(str(exc), 400, {"code": exc.code})
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            return error(
                "供应商名称已存在", 409, {"code": "provider_name_conflict"}
            )
        raise
    return success(
        {
            "provider": store.public_provider(provider),
            "model": _public_model(model),
            "verification": verification,
        },
        201,
    )


@models_bp.post("/providers")
def create_provider():
    raw = payload()
    data, message = _provider_payload(raw)
    if message:
        return error(message)
    if str(raw.get("provider_kind") or "openai_compatible") != "openai_compatible":
        return error(
            "官方供应商请使用供应商配置向导",
            400,
            {"code": "use_provider_bundle"},
        )
    data = {**(data or {}), "status": "unverified"}
    db = get_db()
    store = ModelStore(db)
    try:
        provider = store.create_provider(data or {})
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            return error("供应商名称已存在")
        raise
    _log(db, "model_provider.create", f"添加供应商「{provider.get('name')}」")
    return success(store.public_provider(provider), 201)


@models_bp.put("/providers/<provider_id>")
def update_provider(provider_id: str):
    raw = payload()
    store = ModelStore(get_db())
    existing = store.get_provider(provider_id)
    if not existing:
        return error("供应商不存在", 404)
    data, message = _provider_payload(raw)
    if message:
        return error(message)
    official = existing.get("provider_kind") in {"deepseek", "zhipu", "siliconflow"}
    existing_options = existing.get("options_json") or {}
    incoming_options = (
        data.get("options_json")
        if data and "options_json" in data
        else existing_options
    )
    locked_changed = (
        (data or {}).get("name") != existing.get("name")
        or (data or {}).get("api_url", "").rstrip("/")
        != str(existing.get("api_url") or "").rstrip("/")
        or ("provider_kind" in data and data["provider_kind"] != existing.get("provider_kind"))
        or ("api_mode" in data and data["api_mode"] != existing.get("api_mode"))
        or (
            isinstance(incoming_options, dict)
            and incoming_options.get("thinking_style")
            != existing_options.get("thinking_style")
        )
    )
    if official and locked_changed:
        return error(
            "官方供应商的名称、地址与协议由系统维护",
            400,
            {"code": "official_field_locked"},
        )
    provider = store.update_provider(provider_id, data or {})
    return success(store.public_provider(provider))


@models_bp.post("/providers/<provider_id>/models")
def create_model(provider_id: str):
    data, message = _model_payload(payload())
    if message:
        return error(message)
    store = ModelStore(get_db())
    provider = store.get_provider(provider_id)
    if not provider:
        return error("供应商不存在", 404)
    model_data = {**(data or {}), "status": "unverified"}
    verification = None
    if model_data.get("type") == "chat":
        probe = {
            "id": "model-add-check",
            "name": model_data["name"],
            "api_model": model_data.get("api_model") or model_data["name"],
            "api_url": provider.get("api_url") or "",
            "api_key": provider.get("api_key") or "",
            "provider_kind": provider.get("provider_kind") or "openai_compatible",
            "api_mode": provider.get("api_mode") or "chat_completions",
            "capabilities_json": {"chat": True, "stream": True, "tools": True},
            "request_options_json": model_data.get("request_options_json") or {},
            "provider_options_json": provider.get("options_json") or {},
        }
        model_data['id'] = uuid.uuid4().hex
        probe.update(id=model_data['id'], provider_id=provider_id,
                     provider_name=provider['name'])
        verification = asyncio.run(_run_agent_contract(probe)).to_dict()
        model_data["status"] = "available" if verification["passed"] else "unverified"
        model_data["capabilities_json"] = {
            name: check.get("status") == "passed"
            for name, check in verification["checks"].items()
        }
    model = store.create_model(provider_id, model_data)
    if not model:
        return error("供应商不存在", 404)
    if verification is not None:
        store.save_capability_checks(model["id"], verification["checks"])
        model = store.apply_model_verification(model["id"], verification["passed"])
    return success(_public_model(model), 201)


@models_bp.patch("/<model_id>/default")
def set_default_model(model_id: str):
    model = ModelStore(get_db()).set_default_model(model_id)
    if not model:
        return error("模型不存在、不可用或不是对话模型", 404)
    return success(_public_model(model))


@models_bp.put("/<model_id>")
def update_model(model_id: str):
    data, message = _model_payload(payload())
    if message:
        return error(message)
    model = ModelStore(get_db()).update_model(model_id, data or {})
    if not model:
        return error("模型不存在", 404)
    return success(_public_model(model))


@models_bp.delete("/<model_id>")
def delete_model(model_id: str):
    db = get_db()
    deleted = ModelStore(db).delete_model(model_id)
    if not deleted:
        return error("模型不存在", 404)
    _log(db, "model.delete", f"删除模型 {model_id}")
    return success(None, 204)


@models_bp.delete("/providers/<provider_id>")
def delete_provider(provider_id: str):
    db = get_db()
    deleted = ModelStore(db).delete_provider(provider_id)
    if not deleted:
        return error("供应商不存在", 404)
    _log(db, "model_provider.delete", f"删除供应商 {provider_id}")
    return success(None, 204)
