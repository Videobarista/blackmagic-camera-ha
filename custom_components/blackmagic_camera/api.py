"""Async client for the Blackmagic camera REST API.

The Camera Control REST API is served by the camera's built-in web media
manager under ``/control/api/v1``. State changes are pushed over a websocket
at ``/control/websocket``. This module only talks HTTP; Home Assistant
specific logic lives in the coordinator.
"""

from __future__ import annotations

import asyncio
import base64
from http import HTTPStatus
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import API_BASE_PATH, REQUEST_TIMEOUT, WEBSOCKET_PATHS

_LOGGER = logging.getLogger(__name__)

# Status codes that mean "this property exists on this camera".
_SUPPORTED_STATUS = frozenset({HTTPStatus.OK, HTTPStatus.NO_CONTENT, HTTPStatus.FORBIDDEN})


class BlackmagicCameraError(Exception):
    """Base error for the Blackmagic camera client."""


class CannotConnect(BlackmagicCameraError):
    """The camera could not be reached."""


class InvalidAuth(BlackmagicCameraError):
    """The camera rejected the credentials."""


class HttpsRequired(BlackmagicCameraError):
    """The camera redirects plain HTTP to HTTPS ("Enabled with security only")."""


class WebsocketUnsupported(BlackmagicCameraError):
    """The camera does not offer the notification websocket."""


class ApiError(BlackmagicCameraError):
    """The camera answered with an error status."""

    def __init__(self, status: int, method: str, path: str, message: str = "") -> None:
        """Initialize the error."""
        self.status = status
        self.method = method
        self.path = path
        self.message = message
        detail = f": {message}" if message else ""
        super().__init__(f"{method} {path} returned HTTP {status}{detail}")


class BlackmagicCameraApi:
    """Minimal client for the Blackmagic camera REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        *,
        use_https: bool = False,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._host = host.strip().rstrip("/")
        self._use_https = use_https
        self._basic_auth: str | None = None
        if username:
            token = base64.b64encode(f"{username}:{password or ''}".encode()).decode()
            self._basic_auth = f"Basic {token}"
        # Only send basic credentials once the camera asked for them; a
        # digest challenge is answered by the session middleware instead.
        self._send_basic = False
        self._ws_path: str | None = None

    @property
    def host(self) -> str:
        """Return the configured host."""
        return self._host

    @property
    def base_url(self) -> str:
        """Return the base URL of the REST API."""
        scheme = "https" if self._use_https else "http"
        return f"{scheme}://{self._host}{API_BASE_PATH}"

    @property
    def web_url(self) -> str:
        """Return the URL of the camera's web media manager."""
        scheme = "https" if self._use_https else "http"
        return f"{scheme}://{self._host}/"

    def _websocket_url(self, path: str) -> str:
        scheme = "wss" if self._use_https else "ws"
        return f"{scheme}://{self._host}{path}"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{quote(path, safe='/')}"

    async def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        allow_basic_retry: bool = True,
    ) -> tuple[int, Any]:
        """Perform a request and return (status, decoded body)."""
        kwargs: dict[str, Any] = {
            "timeout": aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            # A redirect means HTTPS only or a login page, never the API.
            "allow_redirects": False,
        }
        if payload is not None:
            kwargs["json"] = payload
        if self._send_basic and self._basic_auth is not None:
            kwargs["headers"] = {"Authorization": self._basic_auth}

        try:
            async with self._session.request(method, self._url(path), **kwargs) as resp:
                status = resp.status
                if HTTPStatus.MULTIPLE_CHOICES <= status < HTTPStatus.BAD_REQUEST:
                    location = resp.headers.get("Location", "")
                    if location.lower().startswith("https:") and not self._use_https:
                        raise HttpsRequired(f"{self._host} only accepts HTTPS ({location})")
                    raise ApiError(status, method, path, f"redirected to {location}")
                if status == HTTPStatus.UNAUTHORIZED:
                    challenge = resp.headers.get("WWW-Authenticate", "")
                    if (
                        allow_basic_retry
                        and self._basic_auth is not None
                        and not self._send_basic
                        and challenge.lower().startswith("basic")
                    ):
                        self._send_basic = True
                        return await self._request(method, path, payload, allow_basic_retry=False)
                    raise InvalidAuth(f"{method} {path} requires valid credentials")
                body: Any = None
                if status != HTTPStatus.NO_CONTENT:
                    text = await resp.text()
                    if text:
                        try:
                            body = await resp.json(content_type=None)
                        except ValueError:
                            body = text
                return status, body
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CannotConnect(f"Error talking to {self._host}: {err}") from err

    async def _call(self, method: str, path: str, payload: Any = None) -> Any:
        status, body = await self._request(method, path, payload)
        if status >= HTTPStatus.BAD_REQUEST:
            message = ""
            if isinstance(body, dict):
                message = str(body.get("error") or body.get("message") or "")
            elif isinstance(body, str):
                message = body[:200]
            raise ApiError(status, method, path, message)
        return body

    async def get(self, path: str) -> Any:
        """GET a property and return the decoded JSON body."""
        return await self._call("GET", path)

    async def put(self, path: str, payload: Any = None) -> Any:
        """PUT a property."""
        return await self._call("PUT", path, payload)

    async def post(self, path: str, payload: Any = None) -> Any:
        """POST to a property."""
        return await self._call("POST", path, payload)

    async def probe(self, path: str) -> tuple[bool, Any]:
        """Return whether a property exists on this camera and its value."""
        status, body = await self._request("GET", path)
        if status in _SUPPORTED_STATUS:
            return True, body if status == HTTPStatus.OK else None
        _LOGGER.debug("Property %s not supported (HTTP %s)", path, status)
        return False, None

    async def get_product(self) -> dict[str, Any]:
        """Return product information, used to validate the connection."""
        body = await self.get("/system/product")
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.OK, "GET", "/system/product", "unexpected response")
        return body

    async def ws_connect(self) -> aiohttp.ClientWebSocketResponse:
        """Open the notification websocket.

        The first path that answers is remembered; when no path offers a
        websocket, WebsocketUnsupported is raised.
        """
        paths = (self._ws_path,) if self._ws_path else WEBSOCKET_PATHS
        for path in paths:
            try:
                ws = await self._ws_connect_path(path)
            except WebsocketUnsupported:
                _LOGGER.debug("No websocket at %s on %s", path, self._host)
                continue
            self._ws_path = path
            return ws
        raise WebsocketUnsupported(f"No notification websocket found on {self._host}")

    async def _ws_connect_path(self, path: str) -> aiohttp.ClientWebSocketResponse:
        # A 10 s heartbeat notices a camera that was switched off within ~15 s.
        kwargs: dict[str, Any] = {"heartbeat": 10, "timeout": aiohttp.ClientWSTimeout(ws_close=10)}
        if self._send_basic and self._basic_auth is not None:
            kwargs["headers"] = {"Authorization": self._basic_auth}
        try:
            return await self._session.ws_connect(self._websocket_url(path), **kwargs)
        except aiohttp.WSServerHandshakeError as err:
            if err.status == HTTPStatus.UNAUTHORIZED:
                raise InvalidAuth("Websocket requires valid credentials") from err
            if err.status in (
                HTTPStatus.BAD_REQUEST,
                HTTPStatus.NOT_FOUND,
                HTTPStatus.METHOD_NOT_ALLOWED,
                HTTPStatus.NOT_IMPLEMENTED,
            ):
                raise WebsocketUnsupported(f"Websocket not offered: HTTP {err.status}") from err
            raise CannotConnect(f"Websocket handshake failed: {err}") from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CannotConnect(f"Websocket connection failed: {err}") from err


async def gather_limited(limit: int, *coros: Any) -> list[Any]:
    """Run coroutines with a concurrency limit, returning results or exceptions."""
    semaphore = asyncio.Semaphore(limit)

    async def _run(coro: Any) -> Any:
        async with semaphore:
            return await coro

    return await asyncio.gather(*(_run(c) for c in coros), return_exceptions=True)
