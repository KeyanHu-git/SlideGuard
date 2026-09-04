from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "src" / "slideguard"

FORBIDDEN_IMPORTS = {
    "aiohttp",
    "ftplib",
    "http",
    "httpx",
    "requests",
    "smtplib",
    "socket",
    "telnetlib",
    "urllib.request",
    "urllib3",
    "webbrowser",
    "websockets",
    "xmlrpc.client",
}
FORBIDDEN_DEPENDENCIES = {
    "aiohttp",
    "amplitude-analytics",
    "httpx",
    "mixpanel",
    "opentelemetry-api",
    "opentelemetry-sdk",
    "posthog",
    "requests",
    "segment-analytics-python",
    "sentry-sdk",
    "urllib3",
    "websockets",
}
FORBIDDEN_PROCESS_TOKENS = re.compile(
    r"(?i)(?:^|[^a-z0-9_-])(?:curl|wget|Invoke-WebRequest|Invoke-RestMethod|"
    r"Start-BitsTransfer|System\.Net\.WebClient|TcpClient|UdpClient)(?:[^a-z0-9_-]|$)"
)


def _finding(path: Path, line: int, code: str, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "path": path.relative_to(ROOT).as_posix(),
        "line": line,
        "detail": detail,
    }


def _is_forbidden_import(name: str) -> bool:
    return any(name == blocked or name.startswith(f"{blocked}.") for blocked in FORBIDDEN_IMPORTS)


def _python_findings(path: Path) -> list[dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_import(alias.name):
                    findings.append(_finding(path, node.lineno, "NETWORK_IMPORT", alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_forbidden_import(module):
                findings.append(_finding(path, node.lineno, "NETWORK_IMPORT", module))
        elif isinstance(node, ast.Call) and node.args:
            call_name = ""
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            if call_name in {"__import__", "import_module"}:
                value = node.args[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and _is_forbidden_import(value.value):
                    findings.append(_finding(path, node.lineno, "DYNAMIC_NETWORK_IMPORT", value.value))
            elif call_name == "openUrl":
                value = node.args[0]
                local_file_only = (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "fromLocalFile"
                )
                if not local_file_only:
                    findings.append(_finding(path, node.lineno, "NONLOCAL_DESKTOP_URL", "openUrl"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = FORBIDDEN_PROCESS_TOKENS.search(node.value)
            if match:
                findings.append(_finding(path, getattr(node, "lineno", 1), "NETWORK_PROCESS", match.group(0).strip()))
    return findings


def _powershell_findings(path: Path) -> list[dict[str, Any]]:
    findings = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        match = FORBIDDEN_PROCESS_TOKENS.search(line)
        if match:
            findings.append(_finding(path, line_number, "NETWORK_PROCESS", match.group(0).strip()))
    return findings


def _declared_dependencies() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^dependencies\s*=\s*(\[.*?^\])", text)
    if not match:
        raise RuntimeError("Cannot locate project.dependencies in pyproject.toml")
    values = ast.literal_eval(match.group(1))
    return [re.split(r"[<>=!~ ;\[]", item, maxsplit=1)[0].casefold() for item in values]


def main() -> int:
    python_files = sorted(RUNTIME_ROOT.rglob("*.py"))
    powershell_files = sorted((RUNTIME_ROOT / "resources").glob("*.ps1"))
    findings: list[dict[str, Any]] = []
    for path in python_files:
        findings.extend(_python_findings(path))
    for path in powershell_files:
        findings.extend(_powershell_findings(path))

    dependencies = _declared_dependencies()
    for dependency in dependencies:
        if dependency in FORBIDDEN_DEPENDENCIES:
            findings.append(_finding(ROOT / "pyproject.toml", 1, "NETWORK_DEPENDENCY", dependency))

    findings.sort(key=lambda item: (item["path"], item["line"], item["code"], item["detail"]))
    report = {
        "schemaVersion": "1.0",
        "verdict": "PASS" if not findings else "FAIL",
        "policy": {
            "mode": "offline-only",
            "telemetryEnabled": False,
            "automaticUploadsEnabled": False,
            "updateChecksEnabled": False,
        },
        "scope": {
            "runtimePythonFiles": len(python_files),
            "runtimePowerShellFiles": len(powershell_files),
            "directDependencies": len(dependencies),
        },
        "findings": findings,
    }
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
