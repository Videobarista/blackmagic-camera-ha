"""Async client for the Blackmagic camera REST API.

The Camera Control REST API is served by the camera's built-in web media
manager under ``/control/api/v1``. State changes are pushed over a websocket
at ``/control/websocket``. This module only talks HTTP; Home Assistant
specific logic lives in the coordinator.

Everything the camera sends is treated as untrusted: responses are size
limited, then decoded and bounded by parsing.py (text length, list length,
nesting) before anything else sees them.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from http import HTTPStatus
import logging
import ssl
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import (
    API_BASE_PATH,
    MAX_RESPONSE_BYTES,
    MAX_TEXT_LENGTH,
    REQUEST_TIMEOUT,
    WEBSOCKET_PATHS,
)
from .parsing import content_length, decode_body, format_fingerprint, split_host_port

_LOGGER = logging.getLogger(__name__)

# Status codes that mean "this property exists on this camera".
_SUPPORTED_STATUS = frozenset({HTTPStatus.OK, HTTPStatus.NO_CONTENT, HTTPStatus.FORBIDDEN})
_CHUNK_SIZE = 64 * 1024


class BlackmagicCameraError(Exception):
    """Base error for the Blackmagic camera client."""


class CannotConnect(BlackmagicCameraError):
    """The camera could not be reached."""


class CertificateMismatch(CannotConnect):
    """The camera presents a different TLS certificate than the pinned one."""

    def __init__(self, host: str, fingerprint: str) -> None:
        """Initialize the error with the fingerprint the camera presented."""
        self.fingerprint = fingerprint
        shown = format_fingerprint(fingerprint)
        super().__init__(f"{host} presents a different TLS certificate (SHA-256 {shown})")


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


async def async_fetch_fingerprint(host: str, ssl_context: ssl.SSLContext) -> str:
    """Return the SHA-256 fingerprint of the certificate the camera presents."""
    hostname, port = split_host_port(host, 443)
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            _reader, writer = await asyncio.open_connection(hostname, port, ssl=ssl_context)
    except (OSError, TimeoutError) as err:
        detail = str(err) or type(err).__name__
        raise CannotConnect(f"TLS connection to {host} failed: {detail}") from err
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        certificate = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError as err:
            _LOGGER.debug("Closing the TLS connection to %s: %s", host, err)
    if not certificate:
        raise CannotConnect(f"{host} did not present a TLS certificate")
    return hashlib.sha256(certificate).hexdigest()


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
        fingerprint: str | None = None,
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
        self._ssl: aiohttp.Fingerprint | None = None
        self.set_fingerprint(fingerprint)

    @property
    def host(self) -> str:
        """Return the configured host."""
        return self._host

    @property
    def use_https(self) -> bool:
        """Return True when the camera is reached over HTTPS."""
        return self._use_https

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

    def set_fingerprint(self, fingerprint: str | None) -> None:
        """Only accept the TLS certificate with this SHA-256 fingerprint (hex)."""
        if fingerprint and self._use_https:
            self._ssl = aiohttp.Fingerprint(bytes.fromhex(fingerprint))
        else:
            self._ssl = None

    def _websocket_url(self, path: str) -> str:
        scheme = "wss" if self._use_https else "ws"
        return f"{scheme}://{self._host}{path}"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{quote(path, safe='/')}"

    async def _read_body(self, resp: aiohttp.ClientResponse, method: str, path: str) -> bytes:
        """Read a response, refusing anything larger than MAX_RESPONSE_BYTES."""
        too_large = f"response larger than {MAX_RESPONSE_BYTES} bytes"
        length = content_length(resp.headers.get("Content-Length", ""))
        if length is not None and length > MAX_RESPONSE_BYTES:
            raise ApiError(resp.status, method, path, too_large)
        chunks: list[bytes] = []
        size = 0
        async for chunk in resp.content.iter_chunked(_CHUNK_SIZE):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ApiError(resp.status, method, path, too_large)
            chunks.append(chunk)
        return b"".join(chunks)

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
        if self._ssl is not None:
            kwargs["ssl"] = self._ssl

        try:
            async with self._session.request(method, self._url(path), **kwargs) as resp:
                status = resp.status
                if HTTPStatus.MULTIPLE_CHOICES <= status < HTTPStatus.BAD_REQUEST:
                    location = resp.headers.get("Location", "")[:MAX_TEXT_LENGTH]
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
                        if not self._use_https:
                            # Basic sends the password readable; never without TLS.
                            raise InvalidAuth(
                                f"{self._host} asks for basic authentication over plain "
                                "HTTP; enable HTTPS to send credentials"
                            )
                        self._send_basic = True
                        return await self._request(method, path, payload, allow_basic_retry=False)
                    raise InvalidAuth(f"{method} {path} requires valid credentials")
                body: Any = None
                if status != HTTPStatus.NO_CONTENT:
                    raw = await self._read_body(resp, method, path)
                    body = decode_body(raw, resp.charset)
                return status, body
        except aiohttp.ServerFingerprintMismatch as err:
            raise CertificateMismatch(self._host, err.got.hex()) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            detail = str(err) or type(err).__name__
            raise CannotConnect(f"Error talking to {self._host}: {detail}") from err

    async def _call(self, method: str, path: str, payload: Any = None) -> Any:
        status, body = await self._request(method, path, payload)
        if status >= HTTPStatus.BAD_REQUEST:
            message = ""
            if isinstance(body, dict):
                message = str(body.get("error") or body.get("message") or "")
            elif isinstance(body, str):
                message = body[:200]
            raise ApiError(status, method, path, message[:200])
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
        kwargs: dict[str, Any] = {
            "heartbeat": 10,
            "timeout": aiohttp.ClientWSTimeout(ws_close=10),
            "max_msg_size": MAX_RESPONSE_BYTES,
        }
        if self._send_basic and self._basic_auth is not None:
            kwargs["headers"] = {"Authorization": self._basic_auth}
        if self._ssl is not None:
            kwargs["ssl"] = self._ssl
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
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
        except aiohttp.ServerFingerprintMismatch as err:
            raise CertificateMismatch(self._host, err.got.hex()) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            detail = str(err) or type(err).__name__
            raise CannotConnect(f"Websocket connection failed: {detail}") from err


async def gather_limited(limit: int, *coros: Any) -> list[Any]:
    """Run coroutines with a concurrency limit, returning results or exceptions."""
    semaphore = asyncio.Semaphore(limit)

    async def _run(coro: Any) -> Any:
        async with semaphore:
            return await coro

    return await asyncio.gather(*(_run(c) for c in coros), return_exceptions=True)
