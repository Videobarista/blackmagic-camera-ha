"""Pure helpers that handle untrusted input: camera answers and host input.

Nothing here talks to the network or Home Assistant, so this module can be
fuzzed on its own (see tests/fuzz/fuzz_camera_data.py).
"""

from __future__ import annotations

from collections.abc import Iterable
import ipaddress
import json
import logging
import math
import re
from typing import Any

from .const import MAX_JSON_DEPTH, MAX_LIST_ITEMS, MAX_SUB_DEVICES, MAX_TEXT_LENGTH

_LOGGER = logging.getLogger(__name__)

# Names the camera reports (displays, panel buttons) end up in URL paths, so
# only plain names are accepted: no "/", "..", "?", "%" or control characters.
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}")
_HOSTNAME = re.compile(
    r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?"
)
# ASCII digits only: str.isdigit() also accepts "²" and friends, which int()
# then refuses.
_DIGITS = re.compile(r"[0-9]{1,10}")


def _port(value: str) -> int | None:
    """Return a valid TCP port, or None."""
    if not _DIGITS.fullmatch(value):
        return None
    port = int(value)
    return port if 1 <= port <= 65535 else None


def format_fingerprint(fingerprint: str) -> str:
    """Return a hex fingerprint as AA:BB:CC:... for display."""
    value = fingerprint.upper()
    return ":".join(value[i : i + 2] for i in range(0, len(value), 2))


def split_host_port(host: str, default_port: int) -> tuple[str, int]:
    """Split "host", "host:port" or "[v6]:port" into name and port."""
    if host.startswith("["):
        address, _, rest = host[1:].partition("]")
        port = _port(rest[1:]) if rest.startswith(":") else None
        return address, port or default_port
    name, separator, port_text = host.rpartition(":")
    if separator and (port := _port(port_text)) is not None:
        return name, port
    return host, default_port


def normalize_host(value: str) -> str | None:
    """Return "name", "name:port" or "[v6]:port", or None when invalid.

    Only an IP address or host name with an optional port is accepted, so
    nothing else (credentials, paths, queries) can slip into the URLs.
    """
    host = value.strip()
    for prefix in ("http://", "https://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix) :]
    host = host.split("/", 1)[0]
    if "%" in host:
        # IPv6 zone IDs ("fe80::1%eth0") do not fit in a URL.
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return f"[{address}]" if address.version == 6 else str(address)

    port: str | None = None
    if host.startswith("["):
        inner, closed, rest = host[1:].partition("]")
        try:
            name = f"[{ipaddress.IPv6Address(inner)}]"
        except ValueError:
            return None
        if not closed or (rest and not rest.startswith(":")):
            return None
        port = rest[1:] if rest else None
    else:
        name, separator, port_part = host.rpartition(":")
        if not separator:
            name = host
        else:
            port = port_part
        if not _HOSTNAME.fullmatch(name):
            return None
    if port is None:
        return name
    number = _port(port)
    return None if number is None else f"{name}:{number}"


def bound_json(value: Any, depth: int = 0) -> Any:
    """Limit what a misbehaving camera can make Home Assistant keep.

    Text is cut to MAX_TEXT_LENGTH (the Home Assistant state limit), lists
    and objects to MAX_LIST_ITEMS entries, nesting to MAX_JSON_DEPTH levels,
    and non-finite numbers (NaN, Infinity) become None.
    """
    if isinstance(value, str):
        return value[:MAX_TEXT_LENGTH]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dict, list)) and depth >= MAX_JSON_DEPTH:
        return None
    if isinstance(value, dict):
        items = list(value.items())[:MAX_LIST_ITEMS]
        return {str(key)[:MAX_TEXT_LENGTH]: bound_json(item, depth + 1) for key, item in items}
    if isinstance(value, list):
        return [bound_json(item, depth + 1) for item in value[:MAX_LIST_ITEMS]]
    return value


def parse_json_text(text: str) -> Any:
    """Decode JSON from the camera; raises ValueError when it is not JSON."""
    try:
        return bound_json(json.loads(text))
    except RecursionError as err:
        # Absurdly deep nesting: json gives up with RecursionError.
        raise ValueError("JSON nested too deeply") from err


def decode_body(raw: bytes, charset: str | None) -> Any:
    """Decode a response body: bounded JSON, or bounded text when not JSON."""
    try:
        text = raw.decode(charset or "utf-8", errors="replace")
    except (LookupError, ValueError):
        # Unknown charset, or one that refuses errors="replace" (idna,
        # undefined): fall back to UTF-8 (found by fuzzing).
        text = raw.decode("utf-8", errors="replace")
    if not text:
        return None
    try:
        return parse_json_text(text)
    except ValueError:
        return text[:MAX_TEXT_LENGTH]


def content_length(value: str) -> int | None:
    """Return a Content-Length header value, or None when it is not a number."""
    return int(value) if _DIGITS.fullmatch(value) else None


def safe_names(values: Iterable[Any]) -> list[str]:
    """Return the plain, unique names among values (at most MAX_SUB_DEVICES)."""
    names: list[str] = []
    for value in values:
        name = str(value) if isinstance(value, (str, int)) else ""
        if not _SAFE_NAME.fullmatch(name):
            _LOGGER.debug("Ignoring unexpected name from the camera: %r", name[:64])
            continue
        if name not in names:
            names.append(name)
    return names[:MAX_SUB_DEVICES]


def bounded_count(value: Any) -> int:
    """Return a count reported by the camera, limited to 0..MAX_SUB_DEVICES."""
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, min(value, MAX_SUB_DEVICES))
