"""Check that a Home Assistant integration only talks to its own device.

Runs with the Python standard library only (no installs), on Python 3.10+.
It reads the integration's manifest and source code and fails when it finds
anything that could send data elsewhere:

1. Manifest: the IoT class is local, no cloud or webhook dependencies, and no
   Python requirements other than the ones explicitly allowed below.
2. No listeners: nothing opens a server, webhook, HTTP view or UDP socket.
3. No other network libraries: only aiohttp (through Home Assistant) and
   asyncio streams are used, and only in the files allowed below.
4. No hard-coded internet addresses: no URLs with a host, public host names
   or IP addresses in the code; every address comes from the configuration.

What it does not cover: Home Assistant itself, and the code of an allowed
requirement. The runtime side (all traffic goes to the configured host) is
checked by the tests.

Usage: python3 scripts/check_local_only.py [path/to/custom_components/<domain>]
"""

from __future__ import annotations

import ast
import ipaddress
import json
from pathlib import Path
import re
import sys

# ---------------------------------------------------------------------------
# Settings per integration
# ---------------------------------------------------------------------------
# Files that may open connections (to the configured device only).
CONNECTION_FILES = {"api.py"}
# Files that may create Home Assistant HTTP client sessions.
SESSION_FILES = {"helpers.py"}
# Names of the integration's own client object (defined in CONNECTION_FILES);
# calls such as self.api.ws_connect() go through it.
CLIENT_NAMES = {"api"}
# Python requirements that are allowed (the device's own protocol library).
ALLOWED_REQUIREMENTS: set[str] = set()
# Literal addresses that are allowed in the code (none needed here).
ALLOWED_ADDRESSES: set[str] = set()

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
FORBIDDEN_DEPENDENCIES = {"cloud", "webhook"}
# Modules that talk to the network on their own, or serve.
FORBIDDEN_MODULES = {
    "aiohttp.web",
    "ftplib",
    "http.client",
    "http.server",
    "httpx",
    "paho",
    "requests",
    "smtplib",
    "socket",
    "socketserver",
    "telnetlib",
    "urllib.request",
    "websockets",
    "xmlrpc",
    "homeassistant.components.cloud",
    "homeassistant.components.http",
    "homeassistant.components.webhook",
    "homeassistant.helpers.httpx_client",
}
# Calls that listen for incoming connections.
LISTEN_CALLS = {
    "create_datagram_endpoint",
    "create_server",
    "create_unix_server",
    "register_view",
    "start_server",
    "start_unix_server",
}
# Calls that open outgoing connections.
CONNECT_CALLS = {"create_connection", "open_connection", "open_unix_connection", "ws_connect"}
SESSION_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "request"}
SESSION_FACTORIES = {"async_create_clientsession", "async_get_clientsession"}

_URL = re.compile(r"\b(?:https?|wss?|ftp|mqtts?|tcp|udp)://([^\s/'\"{}]+)", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_TLDS = "com|net|org|io|dev|app|cloud|ai|co|eu|nl|de|be|uk|fr|info|biz|me|tv|us"
_HOSTNAME = re.compile(rf"(?:[a-z0-9-]+\.)+(?:{_TLDS})", re.IGNORECASE)
# Example and local addresses never leave the network.
_HARMLESS_NETWORKS = [
    ipaddress.ip_network(net)
    for net in ("0.0.0.0/8", "127.0.0.0/8", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
]


def _harmless_ip(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return True  # Not an address (a version number, for instance).
    return any(address in network for network in _HARMLESS_NETWORKS)


def _call_name(node: ast.Call) -> tuple[str, str]:
    """Return (attribute or function name, name of the object it is called on)."""
    func = node.func
    if isinstance(func, ast.Attribute):
        owner = func.value
        if isinstance(owner, ast.Attribute):
            return func.attr, owner.attr
        if isinstance(owner, ast.Name):
            return func.attr, owner.id
        return func.attr, ""
    if isinstance(func, ast.Name):
        return func.id, ""
    return "", ""


def check_manifest(integration: Path) -> list[str]:
    """Check the manifest for cloud use and extra requirements."""
    manifest = json.loads((integration / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    iot_class = str(manifest.get("iot_class", ""))
    if not iot_class.startswith("local_"):
        problems.append(f"manifest.json: iot_class is {iot_class!r}, not local_*")
    dependencies = set(manifest.get("dependencies", [])) | set(
        manifest.get("after_dependencies", [])
    )
    for dependency in sorted(dependencies & FORBIDDEN_DEPENDENCIES):
        problems.append(f"manifest.json: depends on {dependency!r}")
    for requirement in manifest.get("requirements", []):
        name = re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].lower()
        if name not in {r.lower() for r in ALLOWED_REQUIREMENTS}:
            problems.append(f"manifest.json: requirement {requirement!r} is not allowed")
    return problems


def check_source(path: Path, relative: str) -> list[str]:
    """Check one Python file of the integration."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    problems = []

    for node in ast.walk(tree):
        where = f"{relative}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                + [f"{node.module}.{alias.name}" for alias in node.names if node.module]
            )
            for name in names:
                if any(name == m or name.startswith(f"{m}.") for m in FORBIDDEN_MODULES):
                    problems.append(f"{where}: imports {name}")
        elif isinstance(node, ast.Call):
            name, owner = _call_name(node)
            if name in LISTEN_CALLS:
                problems.append(f"{where}: {name}() listens for connections")
            elif (
                name in CONNECT_CALLS
                and path.name not in CONNECTION_FILES
                and owner not in CLIENT_NAMES
            ):
                problems.append(f"{where}: {name}() outside {sorted(CONNECTION_FILES)}")
            elif (
                name in SESSION_METHODS
                and "session" in owner.lower()
                and path.name not in CONNECTION_FILES
            ):
                problems.append(f"{where}: {owner}.{name}() outside {sorted(CONNECTION_FILES)}")
            elif name in SESSION_FACTORIES and path.name not in SESSION_FILES:
                problems.append(f"{where}: {name}() outside {sorted(SESSION_FILES)}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if text in ALLOWED_ADDRESSES:
                continue
            for match in _URL.finditer(text):
                problems.append(f"{where}: hard-coded address {match.group(0)!r}")
            for match in _IPV4.finditer(text):
                if not _harmless_ip(match.group(0)):
                    problems.append(f"{where}: hard-coded IP address {match.group(0)!r}")
            if _HOSTNAME.fullmatch(text.strip()):
                problems.append(f"{where}: hard-coded host name {text!r}")
    return problems


def find_integration(argv: list[str]) -> Path:
    """Return the integration folder (argument, or the only one in custom_components)."""
    if len(argv) > 1:
        return Path(argv[1])
    root = Path(__file__).resolve().parents[1] / "custom_components"
    folders = [p for p in root.iterdir() if (p / "manifest.json").is_file()]
    if len(folders) != 1:
        raise SystemExit(f"Expected one integration in {root}, found {len(folders)}")
    return folders[0]


def main(argv: list[str]) -> int:
    """Run all checks and report."""
    integration = find_integration(argv)
    problems = check_manifest(integration)
    files = sorted(integration.rglob("*.py"))
    for path in files:
        problems += check_source(path, str(path.relative_to(integration.parent)))

    print(f"Local-only check of {integration.name}: {len(files)} Python files")
    if problems:
        for problem in problems:
            print(f"  FAIL {problem}")
        return 1
    print("  OK   manifest: local IoT class, no cloud or webhook, no extra requirements")
    print("  OK   no listeners, webhooks or HTTP views")
    print(f"  OK   connections only from {', '.join(sorted(CONNECTION_FILES))}")
    print("  OK   no hard-coded internet addresses")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
