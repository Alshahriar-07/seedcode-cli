"""JSON-lines backend used by the optional TypeScript terminal interface."""

from __future__ import annotations

import json
import os
import string
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from .config.manager import load_config, save_config
from .core.agents import AgentRegistry
from .core.providers import (
    get_provider,
    provider_label,
    provider_ready,
    provider_requires_key,
    sync_custom_providers,
    visible_provider_ids,
)
from .utils.logger import get_logger
from .workspace import active_workspace, set_workspace
from .memory import list_sessions, load_session

_log = get_logger("tui_backend")
_RECENT_LIMIT = 12
_ENTRY_LIMIT = 300


class TuiBackend:
    """Handle typed UI operations using SeedCode's real Python state."""

    def __init__(
        self,
        config=None,
        workspace: Path | str | None = None,
        persist: Callable[[Any], None] | None = None,
    ) -> None:
        self.config = config if config is not None else load_config()
        self._persist = persist or (save_config if config is None else lambda _config: None)
        sync_custom_providers(self.config)
        self.workspace = str(Path(workspace or active_workspace()).expanduser().resolve())

    def handle(self, request: Any) -> dict[str, Any]:
        request_id = request.get("id") if isinstance(request, dict) else None
        try:
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            method = request.get("method")
            params = request.get("params") or {}
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ValueError("request requires a method and params object")
            handler = getattr(self, f"_op_{method.replace('.', '_')}", None)
            if handler is None:
                raise ValueError(f"unknown method: {method}")
            return {"id": request_id, "ok": True, "result": handler(params)}
        except Exception as exc:
            return {"id": request_id, "ok": False, "error": str(exc)}

    def _op_workspace_current(self, _params: dict[str, Any]) -> dict[str, str]:
        return {"path": self.workspace, "name": Path(self.workspace).name or self.workspace}

    def _op_workspace_recent(self, _params: dict[str, Any]) -> list[str]:
        return list(self.config.recent_workspaces)

    def _op_workspace_roots(self, _params: dict[str, Any]) -> list[str]:
        if os.name != "nt":
            return ["/"]
        return [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").is_dir()]

    def _op_workspace_list(self, params: dict[str, Any]) -> dict[str, Any]:
        raw = str(params.get("path") or self.workspace)
        target = Path(raw).expanduser().resolve()
        if not target.is_dir():
            raise NotADirectoryError(f"not a directory: {target}")
        entries: list[dict[str, str]] = []
        with os.scandir(target) as iterator:
            for entry in iterator:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        entries.append({"name": entry.name, "path": entry.path})
                except OSError:
                    continue
        entries.sort(key=lambda item: item["name"].casefold())
        parent = str(target.parent) if target.parent != target else None
        return {
            "path": str(target),
            "parent": parent,
            "entries": entries[:_ENTRY_LIMIT],
            "truncated": len(entries) > _ENTRY_LIMIT,
        }

    def _op_workspace_select(self, params: dict[str, Any]) -> dict[str, str]:
        selected = set_workspace(str(params.get("path") or ""))
        self.workspace = str(selected)
        recent = [self.workspace, *self.config.recent_workspaces]
        self.config.recent_workspaces = list(dict.fromkeys(recent))[:_RECENT_LIMIT]
        self._persist(self.config)
        return {"path": self.workspace, "name": selected.name or self.workspace}

    def _op_app_status(self, _params: dict[str, Any]) -> dict[str, Any]:
        provider = get_provider(self.config.provider)
        return {
            "workspace": self.workspace,
            "providerId": provider.id,
            "provider": provider.label,
            "model": self.config.model,
            "agent": self.config.agent_profile,
            "configured": self.config.is_configured(),
            "hasKey": bool(self.config.get_api_key(provider.id).strip()),
        }

    def _op_provider_list(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        sync_custom_providers(self.config)
        result = []
        for provider_id in visible_provider_ids(self.config):
            provider = get_provider(provider_id)
            custom = self.config.custom_provider(provider_id)
            model = custom.model if custom else self.config.providers.get(provider_id, {}).model
            result.append(
                {
                    "id": provider.id,
                    "name": provider.label,
                    "status": provider.status,
                    "active": provider_id == self.config.provider,
                    "hasKey": bool(self.config.get_api_key(provider_id).strip()),
                    "keyRequired": provider_requires_key(provider_id),
                    "configured": provider_ready(provider_id, self.config),
                    "model": model,
                    "local": bool(provider.local),
                    "custom": custom is not None,
                    "baseUrl": custom.base_url if custom else provider.extra_settings(self.config).get("host", ""),
                    "enabled": custom.enabled if custom else True,
                    "priority": custom.priority if custom else None,
                }
            )
        return result

    def _op_provider_select(self, params: dict[str, Any]) -> dict[str, str]:
        provider_id = str(params.get("providerId") or "").strip().lower()
        if provider_id not in visible_provider_ids(self.config):
            raise ValueError("provider is not available")
        self.config.active_provider = provider_id
        self._persist(self.config)
        return {"id": provider_id, "name": provider_label(provider_id)}

    def _op_provider_test(self, params: dict[str, Any]) -> dict[str, str]:
        provider_id = str(params.get("providerId") or self.config.provider).strip().lower()
        provider = get_provider(provider_id)
        status = provider.refresh_status(self.config)
        return {"id": provider_id, "name": provider.label, "status": status}

    def _op_provider_update(self, params: dict[str, Any]) -> dict[str, Any]:
        provider_id = str(params.get("providerId") or self.config.provider).strip().lower()
        provider = get_provider(provider_id)
        if "apiKey" in params:
            self.config.set_api_key(provider_id, str(params.get("apiKey") or ""))
        if "host" in params:
            ok, message = provider.set_extra_setting(self.config, "host", str(params["host"]))
            if not ok:
                raise ValueError(message)
        if any(key in params for key in ("name", "baseUrl", "model", "enabled")):
            custom = self.config.update_custom_provider(
                provider_id,
                name=str(params["name"]) if "name" in params else None,
                base_url=str(params["baseUrl"]) if "baseUrl" in params else None,
                model=str(params["model"]) if "model" in params else None,
                enabled=bool(params["enabled"]) if "enabled" in params else None,
            )
            if custom is None and ("baseUrl" in params or provider_id.startswith("custom:")):
                raise ValueError("custom provider not found")
        sync_custom_providers(self.config)
        self._persist(self.config)
        return {"id": provider_id, "hasKey": bool(self.config.get_api_key(provider_id).strip())}

    def _op_provider_move(self, params: dict[str, Any]) -> dict[str, Any]:
        provider_id = str(params.get("providerId") or "").strip().lower()
        try:
            delta = int(params.get("delta") or 0)
        except (TypeError, ValueError):
            raise ValueError("move delta must be an integer") from None
        if delta not in (-1, 1) or not self.config.move_custom_provider(provider_id, delta):
            raise ValueError("custom provider could not be moved")
        self._persist(self.config)
        return {"orderedIds": [entry.id for entry in self.config.ordered_custom_providers()]}

    def _op_provider_add(self, params: dict[str, Any]) -> dict[str, str]:
        entry = self.config.add_custom_provider(
            str(params.get("name") or ""),
            str(params.get("baseUrl") or ""),
            str(params.get("apiKey") or ""),
            str(params.get("model") or ""),
        )
        sync_custom_providers(self.config)
        self._persist(self.config)
        return {"id": entry.id, "name": entry.name}

    def _op_provider_remove(self, params: dict[str, Any]) -> dict[str, bool]:
        provider_id = str(params.get("providerId") or "").strip().lower()
        if not self.config.remove_custom_provider(provider_id):
            raise ValueError("only a saved custom provider can be removed")
        sync_custom_providers(self.config)
        self._persist(self.config)
        return {"removed": True}

    def _op_model_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        provider_id = str(params.get("providerId") or self.config.provider).strip().lower()
        provider = get_provider(provider_id)
        models = provider.list_models(self.config)
        result = []
        for model in models:
            capability_reader = getattr(provider, "model_capabilities", None)
            capabilities = (
                capability_reader(self.config, model.id)
                if callable(capability_reader)
                else None
            )
            result.append({
                "id": model.id,
                "name": model.label,
                "detail": model.detail,
                "isFree": model.is_free,
                "active": model.id == self.config.model if provider_id == self.config.provider else False,
                "capabilities": capabilities,
                "capabilitiesKnown": capabilities is not None,
            })
        return result

    def _op_model_select(self, params: dict[str, Any]) -> dict[str, str]:
        model_id = str(params.get("modelId") or "").strip()
        if not model_id:
            raise ValueError("model id is required")
        self.config.model = model_id
        self._persist(self.config)
        return {"providerId": self.config.provider, "modelId": self.config.model}

    def _op_agent_list(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        registry = AgentRegistry(Path(self.workspace))
        return [
            {
                "id": profile.id,
                "name": profile.name,
                "description": profile.description,
                "tools": sorted(profile.tools) if profile.tools is not None else None,
                "active": profile.id == self.config.agent_profile,
                "source": profile.source,
            }
            for profile in registry.list()
        ]

    def _op_agent_select(self, params: dict[str, Any]) -> dict[str, str]:
        agent_id = str(params.get("agentId") or "").strip().lower()
        profile = AgentRegistry(Path(self.workspace)).get(agent_id)
        if profile is None:
            raise ValueError("agent profile not found")
        self.config.agent_profile = profile.id
        self._persist(self.config)
        return {"id": profile.id, "name": profile.name}

    def _op_session_list(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        provider_ids = set(visible_provider_ids(self.config))
        for provider_id in sorted(provider_ids):
            for session_id, count in list_sessions(provider_id):
                messages = load_session(provider_id, session_id)
                last_user = next(
                    (str(item.get("content") or "") for item in reversed(messages) if item.get("role") == "user"),
                    "",
                )
                rows.append({
                    "id": session_id,
                    "providerId": provider_id,
                    "provider": provider_label(provider_id),
                    "messageCount": count,
                    "preview": " ".join(last_user.split())[:100],
                })
        return sorted(rows, key=lambda row: row["id"], reverse=True)[:100]

    def _op_session_read(self, params: dict[str, Any]) -> list[dict[str, str]]:
        provider_id = str(params.get("providerId") or "").strip().lower()
        session_id = str(params.get("sessionId") or "").strip()
        if provider_id not in visible_provider_ids(self.config) or not session_id:
            raise ValueError("a valid provider and session id are required")
        messages = load_session(provider_id, session_id)
        return [
            {
                "role": str(message.get("role") or "unknown"),
                "text": str(message.get("content") or "")[:4000],
            }
            for message in messages[-12:]
        ]

    def _op_settings_get(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {
            "theme": self.config.theme,
            "streamIdleTimeout": self.config.stream_idle_timeout,
            "maxTokens": self.config.max_tokens,
            "promptProfile": self.config.prompt_profile,
            "autoFailover": self.config.auto_failover,
            "ollamaKeepAlive": self.config.ollama_keep_alive,
        }

    def _op_settings_update(self, params: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "theme": "theme",
            "streamIdleTimeout": "stream_idle_timeout",
            "maxTokens": "max_tokens",
            "promptProfile": "prompt_profile",
            "autoFailover": "auto_failover",
            "ollamaKeepAlive": "ollama_keep_alive",
        }
        candidate = type(self.config).model_validate(self.config.model_dump())
        for key, value in params.items():
            field = allowed.get(key)
            if field is None:
                raise ValueError(f"setting is not editable: {key}")
            setattr(candidate, field, value)
        candidate = type(candidate).model_validate(candidate.model_dump())
        self.config = candidate
        sync_custom_providers(self.config)
        self._persist(self.config)
        return self._op_settings_get({})


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """Serve one request/response JSON object per line; logs stay on stderr."""
    backend = TuiBackend()
    for line in stdin:
        try:
            request = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            response = {"id": None, "ok": False, "error": "invalid JSON request"}
        else:
            response = backend.handle(request)
        try:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
        except (BrokenPipeError, OSError):
            return


__all__ = ["TuiBackend", "serve"]