import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from slideguard.desktop.server import DesktopSession, strict_message, serve, MAX_REQUEST


@pytest.mark.parametrize("line", [
    '{}', '[]',
    '{"version":true,"id":"1","method":"state","params":{}}',
    '{"version":1.0,"id":"1","method":"state","params":{}}',
    '{"version":1,"id":"1","method":"shell","params":{}}',
    '{"version":1,"id":"1","method":"state","params":{"a":NaN}}',
    '{"version":1,"id":"1","method":"state","params":{"a":1e999}}',
    '{"version":1,"id":"1","method":"state","params":{"a":1,"a":2}}',
])
def test_strict_protocol_rejects_ambiguous_inputs(line):
    with pytest.raises(ValueError):
        strict_message(line)


def test_stdio_recovers_after_bad_line_without_polluting_json():
    reader = io.StringIO('bad\n' + json.dumps({"version":1,"id":"ok","method":"state","params":{}})+"\n")
    writer = io.StringIO()
    serve(reader, writer)
    lines = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["ok"] is False
    assert lines[1]["id"] == "ok" and lines[1]["result"]["busy"] == ""


def test_oversized_line_is_drained_not_reinterpreted():
    reader = io.StringIO("x" * (MAX_REQUEST+20) + "\n" +
        json.dumps({"version":1,"id":"after","method":"state","params":{}})+"\n")
    writer = io.StringIO()
    serve(reader, writer)
    values = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert len(values) == 2 and values[1]["id"] == "after"


def ready_session(tmp_path):
    session = DesktopSession()
    session.source = tmp_path / "example.pptx"
    session.source.write_bytes(b"test source")
    session.editor.install_reference((100, 100, 900, 500, 1000, 600))
    session.pages = 2
    return session


def test_no_qt_loaded_in_fresh_worker():
    import subprocess
    result = subprocess.run([sys.executable, "-c",
        "import sys; import slideguard.desktop.server; "
        "assert not any(x.startswith('PySide6') for x in sys.modules)"],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_busy_locks_edits_but_allows_state_and_cooperative_cancel(tmp_path):
    session = ready_session(tmp_path)
    release = threading.Event()
    with session.lock:
        session._start("export", lambda: release.wait(2), lambda _: None)
    try:
        before = session.editor.state
        with pytest.raises(ValueError):
            session.dispatch("edit", {"action":"margin","value":5})
        assert session.state()["busy"] == "export"
        session.dispatch("cancel", {})
        assert session.token.is_cancelled and session.editor.state == before
    finally:
        release.set()
        session.thread.join(3)
    assert session.state()["busy"] == ""


def test_gesture_and_effective_pixels_are_existing_model(tmp_path):
    session = ready_session(tmp_path)
    original = session.editor.state
    session.dispatch("edit", {"action":"begin"})
    session.dispatch("edit", {"action":"resize","handle":"nw","x":.2,"y":.3})
    session.dispatch("edit", {"action":"resize","handle":"nw","x":.25,"y":.35})
    session.dispatch("edit", {"action":"end"})
    session.dispatch("edit", {"action":"undo"})
    assert session.editor.state == original
    state = session.dispatch("edit", {"action":"margin","value":2})
    assert state["cropSize"] == [session.editor.pixel_box[2]-session.editor.pixel_box[0],
                                 session.editor.pixel_box[3]-session.editor.pixel_box[1]]


def test_asset_tokens_cannot_read_arbitrary_files_and_keep_alpha(tmp_path):
    session = DesktopSession()
    path = tmp_path / "transparent.png"
    Image.new("RGBA",(200,100),(255,255,255,0)).save(path)
    token = session.register(path)
    with pytest.raises(ValueError):
        session.dispatch("asset", {"id":str(path)})
    with pytest.raises(ValueError):
        session.dispatch("asset", {"id":token,"width":100000})
    import base64
    result = session.dispatch("asset", {"id":token,"width":128})
    with Image.open(io.BytesIO(base64.b64decode(result["data"]))) as image:
        assert image.size == (128,64)
        assert image.getpixel((0,0))[3] == 0


def test_result_provenance_becomes_stale_after_edit(tmp_path):
    session = ready_session(tmp_path)
    session.result_request = session.editor.request(session.source,1,session.output)
    assert session.state()["resultCurrent"]
    session.dispatch("edit", {"action":"margin","value":2})
    assert not session.state()["resultCurrent"]


def test_failed_worker_clears_busy_and_does_not_publish(tmp_path):
    session = ready_session(tmp_path)
    def fail():
        raise RuntimeError("test failure")
    with session.lock:
        session._start("export", fail, lambda value: pytest.fail("must not run"))
    session.thread.join(2)
    assert not session.busy and session.result is None
    assert "test failure" in session.status
def test_live_drag_uses_gesture_origin_and_cancels(tmp_path):
    session = ready_session(tmp_path)
    original = session.state()["base"]
    session.dispatch("edit", {"action": "begin"})
    session.dispatch("edit", {"action": "drag", "handle": "move", "dx": .02, "dy": .03})
    moved = session.dispatch("edit", {"action": "drag", "handle": "move", "dx": .04, "dy": .05})
    assert moved["base"][0] == pytest.approx(original[0] + .04)
    assert moved["base"][1] == pytest.approx(original[1] + .05)
    with pytest.raises(ValueError, match="拖动"):
        session.dispatch("page", {"page": 2})
    restored = session.dispatch("edit", {"action": "end", "cancel": True})
    assert restored["base"] == original


def test_live_resize_records_one_undo(tmp_path):
    session = ready_session(tmp_path)
    original = session.state()["base"]
    session.dispatch("edit", {"action": "begin"})
    for x in [.7, .6, .5]:
        session.dispatch("edit", {"action": "drag", "handle": "se", "x": x, "y": .7})
    final = session.dispatch("edit", {"action": "end"})
    assert final["base"][2] == .5
    restored = session.dispatch("edit", {"action": "undo"})
    assert restored["base"] == original
