"""Thin HTTP client for the STS2MCP REST API.

Upstream mod: https://github.com/Gennadiyev/STS2MCP
Endpoints used here (singleplayer only for first version):

- GET  /api/v1/singleplayer?format=json   -> current game state JSON
- POST /api/v1/singleplayer               -> perform an action

This layer is policy-free: it only does HTTP, JSON serialization, error
classification, and the post-action settle step. Action allow-listing,
parameter validation, and loop detection live in the tool bridge (F-006).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SP_PATH = "/api/v1/singleplayer"


class GameClientError(Exception):
    """Base error for any STS2MCP REST failure."""


class GameHTTPError(GameClientError):
    """HTTP transport / status error from STS2MCP."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ActionError(GameClientError):
    """Mod returned ``{"status": "error", ...}`` for a POSTed action."""

    def __init__(self, action: str, params: dict[str, Any], response: dict[str, Any]):
        self.action = action
        self.params = params
        self.response = response
        super().__init__(
            f"action {action!r} rejected: {response.get('message', response)!r}"
        )


class GameClient:
    """Thin REST client over STS2MCP's singleplayer API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client or httpx.Client(
            base_url=self.base_url, timeout=timeout
        )
        self._owns_client = client is None

    def __enter__(self) -> "GameClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self) -> dict[str, Any]:
        """GET ``/`` — returns the mod's hello-world payload.

        Useful for ``slay2agent inspect`` reachability checks; on a healthy
        mod it returns ``{"message": "Hello from STS2 MCP v...", "status": "ok"}``.
        """
        return self._json("GET", "/")

    def get_state(self, *, fmt: str = "json") -> dict[str, Any]:
        """GET current singleplayer game state.

        Always returns a dict. Top-level fields include ``state_type``,
        and (when in a run) ``run`` and ``player``.
        """
        return self._json("GET", _SP_PATH, params={"format": fmt})

    def post_action(self, action: str, **params: Any) -> dict[str, Any]:
        """POST a singleplayer action.

        Returns the raw response dict. Raises ``ActionError`` if the mod
        returns ``{"status": "error", ...}``. Drops ``None`` parameters
        so optional fields like ``target`` aren't sent when unused.
        """
        body: dict[str, Any] = {"action": action}
        body.update({k: v for k, v in params.items() if v is not None})
        resp = self._json("POST", _SP_PATH, json=body)
        if resp.get("status") == "error":
            logger.error(
                "STS2MCP rejected action %s params=%s response=%s",
                action,
                {k: v for k, v in params.items() if v is not None},
                resp,
            )
            raise ActionError(action, params, resp)
        return resp

    def post_action_and_settle(
        self,
        action: str,
        *,
        settle_delay: float = 0.05,
        **params: Any,
    ) -> dict[str, Any]:
        """POST an action, briefly wait, then re-read state.

        STS2MCP responds synchronously, but a tiny pause before re-reading
        guards against the "end_turn -> get_state shows old hand" race seen
        when the engine still has a queued animation tick. Callers needing
        stricter convergence can poll ``get_state`` themselves.
        """
        self.post_action(action, **params)
        if settle_delay > 0:
            time.sleep(settle_delay)
        return self.get_state()

    def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            resp = self._client.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            logger.error("STS2MCP %s %s failed: %s", method, path, exc)
            raise GameHTTPError(f"{method} {path}: {exc}") from exc
        if resp.status_code >= 400:
            logger.error(
                "STS2MCP %s %s -> HTTP %s body=%s",
                method,
                path,
                resp.status_code,
                resp.text[:500],
            )
            raise GameHTTPError(
                f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}",
                status_code=resp.status_code,
            )
        try:
            return resp.json()
        except ValueError as exc:
            logger.error(
                "STS2MCP %s %s returned non-JSON body=%s", method, path, resp.text[:200]
            )
            raise GameHTTPError(f"{method} {path}: invalid JSON: {exc}") from exc
