from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable

from packaging.requirements import InvalidRequirement, Requirement


ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES_PATH = ROOT / "packaging" / "component-boundaries.json"
SPEC_PATH = ROOT / "packaging" / "slideguard.spec"
GENERATED_FILES = {"MANIFEST.json", "SHA256SUMS"}
LICENSE_PREFIXES = ("license", "copying", "notice", "authors")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def requirement_name(value: str) -> str | None:
    try:
        requirement = Requirement(value)
    except InvalidRequirement:
        return None
    if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
        return None
    return canonical_name(requirement.name)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_files(package_root: Path, *, exclude: set[str] | None = None) -> list[Path]:
    excluded = exclude or set()
    return sorted(
        (
            path
            for path in package_root.rglob("*")
            if path.is_file() and path.relative_to(package_root).as_posix() not in excluded
        ),
        key=lambda path: path.relative_to(package_root).as_posix().casefold(),
    )


def build_manifest(package_root: Path, package_version: str) -> dict[str, Any]:
    from slideguard.offline import offline_policy

    entries = []
    for path in package_files(package_root, exclude=GENERATED_FILES):
        entries.append(
            {
                "path": path.relative_to(package_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schemaVersion": 1,
        "package": "SlideGuard",
        "version": package_version,
        "hashAlgorithm": "SHA-256",
        "networkPolicy": offline_policy(),
        "excludes": ["MANIFEST.json", "SHA256SUMS"],
        "files": entries,
    }


def write_manifest(package_root: Path, package_version: str) -> Path:
    path = package_root / "MANIFEST.json"
    path.write_text(
        json.dumps(build_manifest(package_root, package_version), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def write_sha256sums(package_root: Path) -> Path:
    path = package_root / "SHA256SUMS"
    rows = []
    for item in package_files(package_root, exclude={"SHA256SUMS"}):
        relative = item.relative_to(package_root).as_posix()
        rows.append(f"{sha256_file(item)} *{relative}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return path


def verify_sha256sums(package_root: Path) -> list[str]:
    failures: list[str] = []
    sums = package_root / "SHA256SUMS"
    listed: set[str] = set()
    for line in sums.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) \*(.+)", line)
        if not match:
            failures.append(f"invalid row: {line}")
            continue
        expected, relative = match.groups()
        folded = relative.casefold()
        if folded in listed:
            failures.append(f"duplicate: {relative}")
            continue
        listed.add(folded)
        target = (package_root / Path(relative)).resolve()
        try:
            target.relative_to(package_root.resolve())
        except ValueError:
            failures.append(f"unsafe path: {relative}")
            continue
        if not target.is_file():
            failures.append(f"missing: {relative}")
        elif sha256_file(target) != expected:
            failures.append(f"hash mismatch: {relative}")
    actual = {
        path.relative_to(package_root).as_posix().casefold()
        for path in package_files(package_root, exclude={"SHA256SUMS"})
    }
    for relative in sorted(actual - listed):
        failures.append(f"unlisted: {relative}")
    return failures


def _distribution_map() -> dict[str, metadata.Distribution]:
    result: dict[str, metadata.Distribution] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            result[canonical_name(name)] = distribution
    return result


def _dependency_closure(
    roots: Iterable[str], distributions: dict[str, metadata.Distribution]
) -> tuple[set[str], set[str]]:
    pending = [canonical_name(name) for name in roots]
    found: set[str] = set()
    missing: set[str] = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        distribution = distributions.get(name)
        if distribution is None:
            missing.add(name)
            continue
        found.add(name)
        for requirement in distribution.requires or []:
            child = requirement_name(requirement)
            if child and child not in found:
                pending.append(child)
    return found, missing


def _declared_license(distribution: metadata.Distribution) -> str:
    value = distribution.metadata.get("License-Expression") or distribution.metadata.get("License") or "UNKNOWN"
    first_line = next((line.strip() for line in value.splitlines() if line.strip()), "UNKNOWN")
    return first_line[:240]


def _homepage(distribution: metadata.Distribution) -> str | None:
    direct = distribution.metadata.get("Home-page")
    if direct:
        return direct
    for value in distribution.metadata.get_all("Project-URL") or []:
        if "," in value:
            return value.split(",", 1)[1].strip()
    return None


def _copy_license_files(distribution: metadata.Distribution, destination: Path) -> list[str]:
    copied: list[str] = []
    for entry in distribution.files or []:
        name = Path(str(entry)).name.casefold()
        if not name.startswith(LICENSE_PREFIXES):
            continue
        source = Path(distribution.locate_file(entry))
        if not source.is_file():
            continue
        target_name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(entry))
        target = destination / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(source, target)
        except OSError:
            continue
        copied.append(target.name)
    return sorted(set(copied), key=str.casefold)


def _component(
    distribution: metadata.Distribution,
    *,
    scope: str,
    license_files: list[str],
    extra_properties: dict[str, str] | None = None,
) -> dict[str, Any]:
    name = distribution.metadata.get("Name") or "unknown"
    version = distribution.version
    item: dict[str, Any] = {
        "type": "library",
        "bom-ref": f"pkg:pypi/{canonical_name(name)}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:pypi/{canonical_name(name)}@{version}",
        "scope": scope,
        "licenses": [{"license": {"name": _declared_license(distribution)}}],
        "properties": [
            {"name": "slideguard:bundled", "value": "true" if scope == "required" else "false"},
            {"name": "slideguard:licenseFiles", "value": ",".join(license_files)},
        ],
    }
    homepage = _homepage(distribution)
    if homepage:
        item["externalReferences"] = [{"type": "website", "url": homepage}]
    for property_name, property_value in sorted((extra_properties or {}).items()):
        item["properties"].append({"name": property_name, "value": property_value})
    return item


def _copy_python_license(package_root: Path) -> Path:
    candidates = (
        Path(sys.base_prefix) / "LICENSE_PYTHON.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
    )
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        raise RuntimeError(f"Python license file was not found under {sys.base_prefix}")
    target = package_root / "licenses" / "python" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def write_component_documents(package_root: Path, boundaries: dict[str, Any]) -> tuple[Path, Path]:
    from slideguard import __version__

    distributions = _distribution_map()
    runtime_names, runtime_missing = _dependency_closure(
        boundaries["bundled"]["runtimeDistributionRoots"], distributions
    )
    embedded_roles = {
        canonical_name(item["distribution"]): item["role"]
        for item in boundaries["bundled"].get("embeddedBuildArtifacts", [])
    }
    embedded_names, embedded_missing = _dependency_closure(embedded_roles, distributions)
    embedded_names &= set(embedded_roles)
    build_names, build_missing = _dependency_closure(
        boundaries["buildOnlyDistributionRoots"], distributions
    )
    required_missing = runtime_missing | embedded_missing
    if required_missing:
        raise RuntimeError(f"Runtime distribution metadata missing: {', '.join(sorted(required_missing))}")

    licenses_root = package_root / "licenses"
    runtime_components: list[dict[str, Any]] = []
    notice_rows: list[tuple[str, str, str, list[str]]] = []
    python_license = _copy_python_license(package_root)
    python_component = {
        "type": "framework",
        "bom-ref": f"pkg:generic/python@{sys.version.split()[0]}",
        "name": "Python",
        "version": sys.version.split()[0],
        "scope": "required",
        "licenses": [{"license": {"id": "Python-2.0"}}],
        "properties": [
            {"name": "slideguard:bundled", "value": "true"},
            {
                "name": "slideguard:licenseFiles",
                "value": python_license.relative_to(package_root).as_posix(),
            },
        ],
    }
    notice_rows.append(("Python", sys.version.split()[0], "Python-2.0", [python_license.name]))

    for normalized in sorted(runtime_names | embedded_names):
        distribution = distributions[normalized]
        destination = licenses_root / normalized
        license_files = _copy_license_files(distribution, destination)
        properties = {}
        if normalized in embedded_roles:
            properties["slideguard:role"] = embedded_roles[normalized]
        runtime_components.append(
            _component(
                distribution,
                scope="required",
                license_files=license_files,
                extra_properties=properties,
            )
        )
        notice_rows.append(
            (
                distribution.metadata.get("Name") or normalized,
                distribution.version,
                _declared_license(distribution),
                license_files,
            )
        )

    external_components = []
    for external in boundaries["external"]:
        external_components.append(
            {
                "type": "application" if external["name"] == "Microsoft PowerPoint" else "library",
                "bom-ref": "external:" + canonical_name(external["name"]),
                "name": external["name"],
                "version": "not-bundled",
                "scope": "required" if external["required"] else "optional",
                "properties": [
                    {"name": "slideguard:bundled", "value": "false"},
                    {"name": "slideguard:owner", "value": external["owner"]},
                    {"name": "slideguard:reason", "value": external["reason"]},
                ],
            }
        )

    build_tools = []
    for normalized in sorted(build_names - embedded_names):
        distribution = distributions[normalized]
        build_tools.append(_component(distribution, scope="excluded", license_files=[]))

    all_components = sorted([python_component, *runtime_components, *external_components], key=lambda item: item["bom-ref"])
    serial_basis = json.dumps(all_components, sort_keys=True, separators=(",", ":"))
    serial = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/KeyanHu-git/SlideGuard/" + serial_basis)
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": "pkg:github/KeyanHu-git/SlideGuard",
                "name": "SlideGuard",
                "version": __version__,
                "licenses": [{"license": {"id": "MIT"}}],
            },
            "tools": {"components": build_tools},
            "properties": [
                {"name": "slideguard:missingBuildMetadata", "value": ",".join(sorted(build_missing))},
            ],
        },
        "components": all_components,
    }
    sbom_path = package_root / "sbom.cdx.json"
    sbom_path.write_text(json.dumps(sbom, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    lines = [
        "# Third-party notices",
        "",
        "This file reports the runtime components and Python distributions copied into this SlideGuard directory.",
        "The declared license comes from each installed distribution's package metadata.",
        "Copied license text remains the controlling source when metadata and text differ.",
        "",
    ]
    for name, version, declared, files in sorted(notice_rows, key=lambda row: row[0].casefold()):
        copied = ", ".join(f"`licenses/{canonical_name(name)}/{item}`" for item in files) or "none found"
        lines.extend(
            [
                f"## {name} {version}",
                "",
                f"Declared license: {declared}",
                "",
                f"Copied files: {copied}",
                "",
            ]
        )
    notices_path = package_root / "THIRD_PARTY_NOTICES.md"
    notices_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return sbom_path, notices_path


def _git_revision() -> str | None:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return process.stdout.strip() if process.returncode == 0 else None


def build_info_document(package_version: str) -> dict[str, Any]:
    from slideguard.offline import offline_policy

    return {
        "schemaVersion": 1,
        "package": "SlideGuard",
        "version": package_version,
        "sourceRevision": _git_revision(),
        "networkPolicy": offline_policy(),
        "python": sys.version.split()[0],
        "pyinstaller": metadata.version("PyInstaller"),
        "sourceDateEpoch": os.environ.get("SOURCE_DATE_EPOCH"),
    }


def finalize_package(package_root: Path) -> dict[str, Path]:
    from slideguard import __version__

    package_root = package_root.resolve()
    if not (package_root / "SlideGuard.exe").is_file():
        raise RuntimeError(f"SlideGuard.exe is missing from {package_root}")

    boundaries = json.loads(BOUNDARIES_PATH.read_text(encoding="utf-8"))
    shutil.copyfile(BOUNDARIES_PATH, package_root / "COMPONENT_BOUNDARIES.json")
    shutil.copyfile(ROOT / "LICENSE", package_root / "LICENSE")
    shutil.copyfile(ROOT / "docs" / "portable-package.md", package_root / "PORTABLE_PACKAGE.md")
    external = package_root / "external"
    (external / "poppler" / "Library" / "bin").mkdir(parents=True, exist_ok=True)
    (external / "chromium").mkdir(parents=True, exist_ok=True)
    (external / "README.txt").write_text(
        "External programs are not part of SlideGuard. Read PORTABLE_PACKAGE.md before adding files here.\n",
        encoding="utf-8",
    )

    build_info = build_info_document(__version__)
    build_info_path = package_root / "BUILD-INFO.json"
    build_info_path.write_text(json.dumps(build_info, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    sbom_path, notices_path = write_component_documents(package_root, boundaries)
    manifest_path = write_manifest(package_root, __version__)
    sums_path = write_sha256sums(package_root)
    failures = verify_sha256sums(package_root)
    if failures:
        raise RuntimeError("Generated SHA256SUMS failed verification: " + "; ".join(failures))
    return {
        "package": package_root,
        "manifest": manifest_path,
        "checksums": sums_path,
        "sbom": sbom_path,
        "notices": notices_path,
        "buildInfo": build_info_path,
    }


def freeze(dist_root: Path, work_root: Path) -> Path:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_root),
        "--workpath",
        str(work_root),
        str(SPEC_PATH),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    return dist_root / "SlideGuard"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and audit the SlideGuard one-folder package")
    parser.add_argument("--dist-root", type=Path, default=ROOT / "dist" / "portable")
    parser.add_argument("--work-root", type=Path, default=ROOT / "build" / "pyinstaller")
    parser.add_argument("--finalize-only", type=Path, default=None, metavar="PACKAGE_ROOT")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    package_root = args.finalize_only.resolve() if args.finalize_only else freeze(args.dist_root.resolve(), args.work_root.resolve())
    outputs = finalize_package(package_root)
    for name, path in outputs.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
