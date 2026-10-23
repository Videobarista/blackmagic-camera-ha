"""Data update coordinator for the Blackmagic Camera integration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import timedelta
import json
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ApiError,
    BlackmagicCameraApi,
    BlackmagicCameraError,
    CannotConnect,
    InvalidAuth,
    WebsocketUnsupported,
    gather_limited,
)
from .capabilities import CameraCapabilities, async_probe_capabilities
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_USE_WEBSOCKET,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_WEBSOCKET,
    DOMAIN,
    FAST_PATHS,
    MAX_PARALLEL_REQUESTS,
    OFFLINE_RETRY_MAX,
    P_PRODUCT,
    SAFETY_POLL_EVERY,
    SLOW_POLL_EVERY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

type BlackmagicCameraConfigEntry = ConfigEntry[BlackmagicCameraCoordinator]

# Delay before reading back a property after changing it.
_READ_BACK_DELAY = 0.25
# Websocket events are batched for this long before entities are updated.
_PUSH_DEBOUNCE = 0.2
_WS_RETRY_MIN = 5
_WS_RETRY_MAX = 60
# A camera without (working) websocket is asked again after this long, or
# right away when it comes back online.
_WS_UNSUPPORTED_RETRY = 600

REASON_UNREACHABLE = "unreachable"
REASON_API_UNAVAILABLE = "api_unavailable"
# Answers meaning "this firmware does not know that call".
_NOT_FOUND_STATUS = frozenset({404, 405, 501})


class BlackmagicCameraCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keep the state of one camera up to date.

    State arrives in two ways: properties pushed over the notification
    websocket, and polling for everything the websocket does not cover (or
    for everything when the websocket is unavailable).

    A camera that does not answer is treated as switched off, not as an
    error: entities become unavailable, the Connection sensor turns off and
    a single cheap request checks for its return with an exponential back-off
    (scan interval doubling up to OFFLINE_RETRY_MAX seconds).
    """

    config_entry: BlackmagicCameraConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BlackmagicCameraConfigEntry,
        api: BlackmagicCameraApi,
    ) -> None:
        """Initialize the coordinator."""
        self.scan_interval = timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=self.scan_interval,
            always_update=False,
        )
        self.api = api
        self.capabilities = CameraCapabilities.minimal()
        self.online = False
        # Why the camera is offline: unreachable, or answering without API.
        self.offline_reason: str | None = None
        self.websocket_connected = False
        # Properties changed by the current update; None means "everything".
        self.changed_paths: frozenset[str] | None = None
        self._use_websocket: bool = entry.options.get(CONF_USE_WEBSOCKET, DEFAULT_USE_WEBSOCKET)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._capabilities_verified = False
        self._poll_count = 0
        self._stopping = False
        self._backoff: float | None = None
        self._online_event = asyncio.Event()
        self._ws_wakeup = asyncio.Event()
        self._issue_id = f"api_unavailable_{entry.entry_id}"
        self._pending_push: dict[str, Any] = {}
        self._push_unsub: CALLBACK_TYPE | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ws_requests: dict[int, list[str]] = {}
        self._ws_subscribed: set[str] = set()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    async def async_initialize(self) -> None:
        """Detect features, falling back to the cached set when offline.

        A camera that is switched off must not make the setup fail: entities
        are created from the cached capabilities and become available as soon
        as the camera answers again.
        """
        try:
            caps, data = await async_probe_capabilities(self.api)
        except InvalidAuth as err:
            raise ConfigEntryError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except BlackmagicCameraError as err:
            stored = await self._store.async_load()
            if stored:
                self.capabilities = CameraCapabilities.from_dict(stored)
            self._set_offline(err)
            self.async_set_updated_data({})
            return

        self.capabilities = caps
        self._capabilities_verified = True
        self._set_online()
        self.async_set_updated_data(data)
        await self._store.async_save(caps.as_dict())

    # ------------------------------------------------------------------
    # Online / offline
    # ------------------------------------------------------------------
    @callback
    def _set_online(self) -> None:
        if not self.online:
            if self._capabilities_verified and self.data:
                _LOGGER.info("Camera %s is back online", self.api.host)
            self.online = True
            self._poll_count = 0  # start with a full poll
            self._ws_wakeup.set()
        if self.offline_reason == REASON_API_UNAVAILABLE:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self.offline_reason = None
        self._backoff = None
        self.update_interval = self.scan_interval
        self._online_event.set()

    @callback
    def _set_offline(self, err: BlackmagicCameraError) -> None:
        """Mark the camera offline and back off the next check.

        A camera that answers HTTP but returns 404 for the REST API has the
        API switched off (web media manager disabled, or set to HTTPS only);
        that gets a hint in the log and a repair issue. A camera that does not
        answer at all is simply switched off.
        """
        reason = (
            REASON_API_UNAVAILABLE
            if isinstance(err, ApiError) and err.status == 404
            else REASON_UNREACHABLE
        )
        if reason != self.offline_reason:
            if reason == REASON_API_UNAVAILABLE:
                _LOGGER.info(
                    "Camera %s answers, but its REST API is not available (HTTP 404). Is "
                    "'Web media manager' enabled in Blackmagic Camera Setup? With 'Enabled "
                    "with security only' turn on HTTPS in the integration. Checking every %s s "
                    "with back-off up to %s s",
                    self.api.host,
                    int(self.scan_interval.total_seconds()),
                    OFFLINE_RETRY_MAX,
                )
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    self._issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="api_unavailable",
                    translation_placeholders={
                        "name": self.config_entry.title,
                        "host": self.api.host,
                    },
                )
            else:
                if self.offline_reason == REASON_API_UNAVAILABLE:
                    ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
                _LOGGER.info(
                    "Camera %s is offline (%s); checking every %s s with back-off up to %s s",
                    self.api.host,
                    err,
                    int(self.scan_interval.total_seconds()),
                    OFFLINE_RETRY_MAX,
                )
        self.offline_reason = reason
        self.online = False
        if self._backoff is None:
            self._backoff = self.scan_interval.total_seconds()
        else:
            self._backoff = max(
                min(self._backoff * 2, OFFLINE_RETRY_MAX), self.scan_interval.total_seconds()
            )
        self.update_interval = timedelta(seconds=self._backoff)
        self._online_event.clear()

    @callback
    def async_update_listeners(self) -> None:
        """Update listeners, then forget which properties changed."""
        super().async_update_listeners()
        self.changed_paths = None

    @callback
    def _set_data(self, data: dict[str, Any]) -> None:
        """Store new data and only notify entities whose properties changed."""
        old = self.data or {}
        changed = {key for key, value in data.items() if old.get(key, ...) != value}
        if not changed:
            return
        # Not async_set_updated_data: that would postpone the next poll on
        # every push and starve polling while the camera pushes a lot.
        self.data = data
        self.changed_paths = frozenset(changed)
        self.async_update_listeners()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def _paths_to_poll(self) -> list[str]:
        count = self._poll_count
        self._poll_count += 1
        paths = self.capabilities.state_paths()
        if self.websocket_connected:
            if count % SAFETY_POLL_EVERY == 0:
                return paths
            paths = [p for p in paths if p not in self._ws_subscribed]
        if count % SLOW_POLL_EVERY == 0:
            return paths
        return [p for p in paths if p in FAST_PATHS]

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the current state from the camera."""
        self.changed_paths = None
        if not self._capabilities_verified:
            return await self._async_verify_capabilities()
        if not self.online:
            # One cheap request to see whether the camera is back.
            try:
                await self.api.get(P_PRODUCT)
            except InvalidAuth as err:
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="invalid_auth"
                ) from err
            except BlackmagicCameraError as err:
                return self._still_offline(err)
            self._set_online()
            self.async_update_listeners()

        paths = self._paths_to_poll()
        if not paths:
            return dict(self.data or {})
        results = await gather_limited(MAX_PARALLEL_REQUESTS, *(self.api.get(p) for p in paths))
        old = self.data or {}
        data = dict(old)
        connection_errors = 0
        missing = 0
        last_error: BlackmagicCameraError | None = None
        for path, result in zip(paths, results, strict=True):
            if isinstance(result, CannotConnect):
                connection_errors += 1
                last_error = result
            elif isinstance(result, InvalidAuth):
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="invalid_auth"
                ) from result
            elif isinstance(result, ApiError):
                # The property exists but has no value right now
                # (no lens attached, display unplugged, ...).
                _LOGGER.debug("Property %s unavailable: %s", path, result)
                data[path] = None
                if result.status == 404:
                    missing += 1
                    last_error = result
            elif isinstance(result, BaseException):
                raise UpdateFailed(f"Unexpected error reading {path}: {result!r}") from result
            else:
                data[path] = result

        if last_error is not None and (connection_errors + missing) * 2 > len(paths):
            # Most reads failed: confirm with the product info before calling
            # the camera offline (a few legitimately missing values are fine).
            try:
                await self.api.get(P_PRODUCT)
            except InvalidAuth as err:
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="invalid_auth"
                ) from err
            except BlackmagicCameraError as err:
                return self._still_offline(err)
        if connection_errors:
            _LOGGER.debug(
                "%d of %d reads failed, keeping previous values", connection_errors, len(paths)
            )
        if changed := {k for k in paths if old.get(k, ...) != data.get(k)}:
            self.changed_paths = frozenset(changed)
        return data

    def _still_offline(self, err: BlackmagicCameraError) -> dict[str, Any]:
        """Handle an unanswered check: offline is a state, not an error."""
        previous = (self.online, self.offline_reason)
        self._set_offline(err)
        if previous != (self.online, self.offline_reason):
            self.async_update_listeners()
        return dict(self.data or {})

    async def _async_verify_capabilities(self) -> dict[str, Any]:
        """Probe a camera that was offline when Home Assistant started."""
        try:
            caps, data = await async_probe_capabilities(self.api)
        except InvalidAuth as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="invalid_auth") from err
        except BlackmagicCameraError as err:
            return self._still_offline(err)

        self._capabilities_verified = True
        changed = not caps.same_features(self.capabilities)
        self.capabilities = caps
        await self._store.async_save(caps.as_dict())
        self._set_online()
        if changed:
            _LOGGER.info("Camera %s features changed, reloading entities", self.api.host)
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        else:
            _LOGGER.info("Camera %s is online", self.api.host)
        self.async_update_listeners()
        return data

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    async def async_command(
        self,
        method: str,
        path: str,
        payload: Any = None,
        refresh: Iterable[str] | None = None,
        fallback: tuple[str, str, Any] | None = None,
        fallback_status: frozenset[int] = _NOT_FOUND_STATUS,
    ) -> None:
        """Send a command and read back the affected properties.

        ``fallback`` is an alternative (method, path, payload) that is tried
        when the camera answers with one of ``fallback_status``, e.g. older
        firmware that does not know the preferred call.
        """
        try:
            try:
                await self._async_send(method, path, payload)
            except ApiError as err:
                if fallback is None or err.status not in fallback_status:
                    raise
                _LOGGER.debug("%s answered %s, trying %s", path, err.status, fallback)
                await self._async_send(*fallback)
        except ApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"path": path, "error": str(err)},
            ) from err
        except InvalidAuth as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except CannotConnect as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"host": self.api.host},
            ) from err

        to_refresh = list(refresh) if refresh is not None else [path]
        if to_refresh:
            await asyncio.sleep(_READ_BACK_DELAY)
            await self.async_refresh_paths(to_refresh)

    async def _async_send(self, method: str, path: str, payload: Any) -> None:
        if method == "put":
            await self.api.put(path, payload)
        else:
            await self.api.post(path, payload)

    async def async_refresh_paths(self, paths: Iterable[str]) -> None:
        """Read a few properties and push them to the entities."""
        wanted = [p for p in paths if self.capabilities.supports(p)]
        if not wanted:
            return
        results = await gather_limited(MAX_PARALLEL_REQUESTS, *(self.api.get(p) for p in wanted))
        data = dict(self.data or {})
        for path, result in zip(wanted, results, strict=True):
            if isinstance(result, ApiError):
                data[path] = None
            elif isinstance(result, BaseException):
                _LOGGER.debug("Could not read back %s: %s", path, result)
            else:
                data[path] = result
        self._set_data(data)

    # ------------------------------------------------------------------
    # Websocket
    # ------------------------------------------------------------------
    @callback
    def async_start_websocket(self) -> None:
        """Start listening for pushed state changes."""
        if not self._use_websocket:
            return
        self.config_entry.async_on_unload(self._async_stop_websocket)
        self.config_entry.async_create_background_task(
            self.hass, self._async_websocket_loop(), f"{DOMAIN} websocket {self.api.host}"
        )

    async def _async_stop_websocket(self) -> None:
        self._stopping = True
        if self._push_unsub is not None:
            self._push_unsub()
            self._push_unsub = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

    async def _async_websocket_loop(self) -> None:
        """Keep the push connection up while the camera is online.

        Nothing here ever gives up for good: a camera that rejects or lacks
        the websocket is asked again after a long pause, or immediately once
        it comes back online (e.g. after a reboot or a settings change).
        """
        retry = _WS_RETRY_MIN
        unsupported_logged = False
        while not self._stopping:
            # While the camera is offline the poller checks for its return.
            await self._online_event.wait()
            self._ws_wakeup.clear()
            try:
                self._ws = await self.api.ws_connect()
            except WebsocketUnsupported as err:
                if not unsupported_logged:
                    _LOGGER.info("No websocket on %s (%s), polling instead", self.api.host, err)
                    unsupported_logged = True
                await self._async_ws_pause(_WS_UNSUPPORTED_RETRY)
                continue
            except InvalidAuth:
                _LOGGER.warning(
                    "Websocket of %s rejected the credentials, polling instead", self.api.host
                )
                await self._async_ws_pause(_WS_UNSUPPORTED_RETRY)
                continue
            except CannotConnect as err:
                _LOGGER.debug("Websocket connect to %s failed: %s", self.api.host, err)
                await self._async_ws_pause(retry)
                retry = min(retry * 2, _WS_RETRY_MAX)
                continue

            unsupported_logged = False
            try:
                await self._async_websocket_session(self._ws)
            finally:
                if self.websocket_connected:
                    retry = _WS_RETRY_MIN
                else:
                    retry = min(retry * 2, _WS_RETRY_MAX)
                await self._ws.close()
                self._ws = None
                self._set_websocket_state(False)
            await self._async_ws_pause(retry)

    async def _async_ws_pause(self, seconds: float) -> None:
        """Sleep, but wake up early when the camera comes back online."""
        try:
            async with asyncio.timeout(seconds):
                await self._ws_wakeup.wait()
        except TimeoutError:
            # The normal case: the pause simply ran out.
            _LOGGER.debug("Websocket retry for %s after %s s", self.api.host, seconds)

    async def _async_websocket_session(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Subscribe and handle one websocket connection until it closes."""
        self._ws_requests = {}
        self._ws_subscribed = set()
        paths = self.capabilities.state_paths()
        events = set(self.capabilities.events)
        if events:
            # The camera announces what it can push: one request for all of it.
            batches = [[p for p in paths if p in events]]
        else:
            # Unknown firmware: one request per property, so a property the
            # firmware does not know only fails on its own and stays polled.
            batches = [[p] for p in paths]
        for request_id, batch in enumerate((b for b in batches if b), start=1):
            self._ws_requests[request_id] = batch
            await ws.send_json(
                {
                    "type": "request",
                    "id": request_id,
                    "data": {"action": "subscribe", "properties": batch},
                }
            )
        async for msg in ws:
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break
            if msg.type == aiohttp.WSMsgType.TEXT:
                self._handle_ws_text(msg.data)

    @callback
    def _handle_ws_text(self, raw: str) -> None:
        """Process one websocket text frame."""
        try:
            message = json.loads(raw)
        except ValueError:
            _LOGGER.debug("Ignoring non JSON websocket frame: %s", raw[:200])
            return
        if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
            return

        data: dict[str, Any] = message["data"]
        action = data.get("action")
        if message.get("type") == "response" and action == "subscribe":
            values = data.get("values")
            if isinstance(values, dict):
                self._queue_push(values)
            if data.get("success", True):
                self._ws_subscribed.update(self._ws_requests.get(message.get("id", -1), ()))
                self._ws_subscribed.update(
                    p for p in data.get("properties") or [] if isinstance(p, str)
                )
                self._set_websocket_state(bool(self._ws_subscribed))
        elif action == "propertyValueChanged" and isinstance(data.get("property"), str):
            self._queue_push({data["property"]: data.get("value")})

    @callback
    def _set_websocket_state(self, connected: bool) -> None:
        if connected == self.websocket_connected:
            return
        self.websocket_connected = connected
        _LOGGER.debug("Websocket of %s %s", self.api.host, "subscribed" if connected else "closed")
        if not connected:
            self._ws_subscribed = set()
            if not self._stopping:
                # Find out right away whether the camera went offline.
                self.config_entry.async_create_task(
                    self.hass, self.async_request_refresh(), "blackmagic_camera refresh"
                )
        self.async_update_listeners()

    @callback
    def _queue_push(self, values: dict[str, Any]) -> None:
        tracked = {k: v for k, v in values.items() if self.capabilities.supports(k)}
        if not tracked:
            return
        self._pending_push.update(tracked)
        if self._push_unsub is None:
            self._push_unsub = self.hass.loop.call_later(_PUSH_DEBOUNCE, self._flush_push).cancel

    @callback
    def _flush_push(self) -> None:
        self._push_unsub = None
        if not self._pending_push or self._stopping:
            return
        pending, self._pending_push = self._pending_push, {}
        self._set_data({**(self.data or {}), **pending})
