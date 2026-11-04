"""Fuzz target for everything that handles untrusted input.

The camera's answers (HTTP bodies and their charset, websocket frames, names,
counts, headers) and the host typed in the config flow all pass through
parsing.py. This target feeds arbitrary bytes to those functions and checks
that they never raise anything unexpected and always return bounded results.

Coverage guided with Atheris (supports Python 3.11-3.13 at the time of
writing; it needs nothing else, not even Home Assistant):

    pip install atheris
    python tests/fuzz/fuzz_camera_data.py -max_total_time=300

Without Atheris, tests/test_fuzz.py runs the same target on random and
mutated inputs in every CI run.
"""

from __future__ import annotations

from functools import cache
import importlib
import math
from pathlib import Path
import sys
import types
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "custom_components.blackmagic_camera"
# Characters that must never survive host validation.
_FORBIDDEN_IN_HOST = frozenset("@/?#% \t\r\n\\")
# Charsets a camera could announce, including ones that misbehave.
CHARSETS = (None, "utf-8", "latin-1", "utf-16", "idna", "undefined", "rot13", "no-such-codec")


@cache
def load_parsing() -> types.ModuleType:
    """Import parsing.py; without Home Assistant, skip the package __init__."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        return importlib.import_module(f"{PACKAGE}.parsing")
    except ImportError:
        # Standalone: parsing.py and const.py only need the standard library,
        # so the package __init__ (which needs Home Assistant) is skipped.
        for name, path in (
            ("custom_components", ROOT / "custom_components"),
            (PACKAGE, ROOT / "custom_components" / "blackmagic_camera"),
        ):
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module
        return importlib.import_module(f"{PACKAGE}.parsing")


def _check_bounded(value: Any, limits: Any, depth: int = 0) -> None:
    """Fail when a decoded value is larger or deeper than allowed."""
    if isinstance(value, str):
        assert len(value) <= limits.MAX_TEXT_LENGTH
    elif isinstance(value, float):
        assert math.isfinite(value)
    elif isinstance(value, list):
        assert depth < limits.MAX_JSON_DEPTH
        assert len(value) <= limits.MAX_LIST_ITEMS
        for item in value:
            _check_bounded(item, limits, depth + 1)
    elif isinstance(value, dict):
        assert depth < limits.MAX_JSON_DEPTH
        assert len(value) <= limits.MAX_LIST_ITEMS
        for key, item in value.items():
            assert isinstance(key, str)
            assert len(key) <= limits.MAX_TEXT_LENGTH
            _check_bounded(item, limits, depth + 1)
    else:
        assert value is None or isinstance(value, (bool, int))


def fuzz_one(data: bytes) -> None:
    """Run one input through every parser and check the results."""
    parsing = load_parsing()
    limits = sys.modules[f"{PACKAGE}.const"]
    text = data.decode("utf-8", errors="replace")

    # A camera answer: HTTP body with any announced charset, websocket frame.
    charset = CHARSETS[data[0] % len(CHARSETS)] if data else None
    _check_bounded(parsing.decode_body(data, charset), limits)
    try:
        _check_bounded(parsing.parse_json_text(text), limits)
    except ValueError:
        pass  # Not JSON: the caller ignores the frame.
    length = parsing.content_length(text)
    assert length is None or length >= 0

    # The host typed in the config flow.
    host = parsing.normalize_host(text)
    if host is not None:
        assert not _FORBIDDEN_IN_HOST & set(host)
        _name, port = parsing.split_host_port(host, 443)
        assert 1 <= port <= 65535
    parsing.split_host_port(text, 443)

    # Names and counts the camera reports, used in URL paths and loops.
    names = parsing.safe_names(text.split(","))
    assert len(names) <= limits.MAX_SUB_DEVICES
    for name in names:
        assert "/" not in name
        assert name not in {".", ".."}
        assert name.isascii()
    count = parsing.bounded_count(int.from_bytes(data[:8], "big", signed=True))
    assert 0 <= count <= limits.MAX_SUB_DEVICES


def main() -> None:
    """Run the target under Atheris (coverage guided)."""
    import atheris

    with atheris.instrument_imports(include=[PACKAGE]):
        load_parsing()
    atheris.Setup(sys.argv, fuzz_one)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
