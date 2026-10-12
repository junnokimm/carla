import json
import subprocess
import sys


def run_child(script: str, tmp_path):
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)],
                            capture_output=True, text=True, timeout=4, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


SETUP = """
import json, sys, threading
from pathlib import Path
from dataclasses import asdict
from src.p6.clock import AffineClockMapping, SessionOrigin
from src.p6.writer import GazeSidecarWriter
from src.p6.source import SyntheticGazeSource
origin = SessionOrigin(0, 0)
writer = GazeSidecarWriter(path=Path(sys.argv[1])/'gaze.jsonl', run_id='bounded',
    origin=origin, mapping=AffineClockMapping.unverified('ns', origin), layout=None, queue_capacity=1)
source = SyntheticGazeSource()
"""


def test_failed_worker_and_full_queue_close_is_bounded_and_reports_unwritten(tmp_path):
    report = run_child(SETUP + """
failed = threading.Event()
def fail(envelope):
    failed.set()
    raise OSError('injected disk failure')
writer._write_sample = fail
writer.start()
writer.capture(source.emit(1), receive_monotonic_ns=1, receive_utc_ns=1)
assert failed.wait(1)
writer.capture(source.emit(2), receive_monotonic_ns=2, receive_utc_ns=2)
report = writer.close()
assert writer.close() == report
print(json.dumps(asdict(report)))
""", tmp_path)

    assert report["status"] == "CLOSED_WITH_ERROR"
    assert report["written_samples"] == 0
    assert report["unconfirmed"]["count"] == 2
    assert report["unconfirmed"]["first_sequence"] == 1
    assert report["unconfirmed"]["last_sequence"] == 2
    assert report["file_closed"] and not report["worker_alive"]
    saved = json.loads((tmp_path / "gaze.jsonl.shutdown.json").read_text())
    assert saved["unconfirmed"]["count"] == 2


def test_timeout_does_not_close_file_or_forget_live_writer(tmp_path):
    report = run_child(SETUP + """
entered, release = threading.Event(), threading.Event()
original = writer._write_sample
def block(envelope):
    entered.set()
    assert release.wait(2)
    original(envelope)
writer._write_sample = block
writer.start()
writer.capture(source.emit(1), receive_monotonic_ns=1, receive_utc_ns=1)
assert entered.wait(1)
try:
    writer.close(timeout=0.02)
    raise AssertionError('expected bounded timeout')
except TimeoutError:
    assert writer._thread is not None and writer._thread.is_alive()
    assert not writer._file.closed
release.set()
print(json.dumps(asdict(writer.close(timeout=2))))
""", tmp_path)

    assert report["written_samples"] == 1
    assert report["unconfirmed"] is None
    assert report["file_closed"]


def test_normal_shutdown_drains_and_repeat_close_is_safe(tmp_path):
    report = run_child(SETUP + """
writer.start()
writer.capture(source.emit(1), receive_monotonic_ns=1, receive_utc_ns=1)
report = writer.close()
assert writer.close() == report
try:
    writer.capture(source.emit(2), receive_monotonic_ns=2, receive_utc_ns=2)
    raise AssertionError('closed writer accepted sample')
except RuntimeError:
    pass
print(json.dumps(asdict(report)))
""", tmp_path)
    assert report["status"] == "CLOSED"
    assert report["written_samples"] == 1


def test_p8_abort_reports_failed_writer_and_still_cleans(tmp_path):
    report = run_child("""
import json, sys, threading
from pathlib import Path
from src.p8.assignment import development_assignment
from src.p8.orchestrator import SessionManager
from tests.test_p8_transition_guards import enter_m1
session = SessionManager(Path(sys.argv[1])).create(development_assignment(), 'writer-error')
enter_m1(session)
writer = session.components.p6.gaze
failed = threading.Event()
def fail(envelope):
    failed.set()
    raise OSError('injected P8 gaze writer failure')
writer._write_sample = fail
session.components.tick()
assert failed.wait(1)
session.components.tick()
session.abort('TEST_ABORT')
print(json.dumps({'closed': session.closed, 'status':session.result['completion_status'],
                  'unconfirmed':writer.shutdown_report.unconfirmed.count,
                  'resources':session.result['components']['owned_resource_tokens']}))
""", tmp_path)
    assert report == {"closed": True, "status": "ABORTED", "unconfirmed": 2, "resources": []}
