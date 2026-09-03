from __future__ import annotations

import html
import xml.etree.ElementTree as ET
from pathlib import Path

from .model import JobReport, Verdict
from .util import write_json


def write_reports(report: JobReport, output_dir: Path) -> None:
    data = report.to_dict()
    write_json(output_dir / "qa-report.json", data)
    _write_html(data, output_dir / "report.html")
    _write_junit(data, output_dir / "junit.xml")


def _write_html(data: dict, path: Path) -> None:
    counts = {status: 0 for status in ("PASS", "PASS_WITH_SOURCE_WARNINGS", "FAIL", "N/A")}
    rows = []
    for finding in data["findings"]:
        counts[finding["status"]] = counts.get(finding["status"], 0) + 1
        evidence = "<br>".join(html.escape(item) for item in finding.get("evidence") or [])
        rows.append(
            "<tr class='{status}'><td>{status}</td><td>{code}</td><td>{slide}</td>"
            "<td>{validator}</td><td>{message}</td><td>{actual}</td><td>{threshold}</td><td>{evidence}</td></tr>".format(
                status=html.escape(finding["status"]), code=html.escape(finding["code"]),
                slide=html.escape(str(finding.get("slide") or "")),
                validator=html.escape(finding["validator"]), message=html.escape(finding["message"]),
                actual=html.escape(str(finding.get("actual") if finding.get("actual") is not None else "")),
                threshold=html.escape(str(finding.get("threshold") if finding.get("threshold") is not None else "")),
                evidence=evidence,
            )
        )
    artifact_rows = "".join(
        f"<tr><td>{html.escape(a['kind'])}</td><td>{html.escape(a['path'])}</td><td>{a['bytes']:,}</td><td><code>{a['sha256']}</code></td></tr>"
        for a in data["artifacts"]
    )
    css = """
body{font:14px/1.5 Segoe UI,Arial,sans-serif;margin:32px;color:#172033;background:#f6f8fb}
h1,h2{color:#10223f}.hero{background:#fff;border:1px solid #dbe3ef;border-radius:12px;padding:22px}
.PASS{background:#edf9f1}.FAIL{background:#fff0f0}.PASS_WITH_SOURCE_WARNINGS{background:#fff8e7}
.badge{display:inline-block;padding:4px 9px;border-radius:999px;margin-right:8px;background:#e8eef8}
table{width:100%;border-collapse:collapse;background:#fff;margin:14px 0 28px}th,td{padding:8px 10px;border:1px solid #dbe3ef;text-align:left;vertical-align:top}
th{background:#edf2f8;position:sticky;top:0}code{font-size:12px;word-break:break-all}
"""
    body = f"""<!doctype html><meta charset='utf-8'><title>SlideGuard QA</title><style>{css}</style>
<main><section class='hero'><h1>SlideGuard QA: {html.escape(data['verdict'])}</h1>
<p><b>Job</b> <code>{html.escape(data['job_id'])}</code><br><b>Source SHA-256</b> <code>{data['source_sha256_after']}</code></p>
<span class='badge'>PASS {counts['PASS']}</span><span class='badge'>WARN {counts['PASS_WITH_SOURCE_WARNINGS']}</span><span class='badge'>FAIL {counts['FAIL']}</span></section>
<h2>Artifacts</h2><table><tr><th>Kind</th><th>Path</th><th>Bytes</th><th>SHA-256</th></tr>{artifact_rows}</table>
<h2>Checks</h2><table><tr><th>Status</th><th>Code</th><th>Slide</th><th>Validator</th><th>Message</th><th>Actual</th><th>Threshold</th><th>Evidence</th></tr>{''.join(rows)}</table></main>"""
    path.write_text(body, encoding="utf-8")


def _write_junit(data: dict, path: Path) -> None:
    failures = sum(1 for item in data["findings"] if item["status"] == "FAIL")
    suite = ET.Element("testsuite", name="SlideGuard", tests=str(len(data["findings"])), failures=str(failures))
    for finding in data["findings"]:
        case = ET.SubElement(suite, "testcase", classname=finding["validator"], name=f"{finding['code']}[slide={finding.get('slide')}]")
        if finding["status"] == "FAIL":
            node = ET.SubElement(case, "failure", message=finding["message"], type=finding["code"])
            node.text = str(finding)
        elif finding["status"] in {"N/A", "PASS_WITH_SOURCE_WARNINGS"}:
            ET.SubElement(case, "skipped", message=finding["message"])
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)

