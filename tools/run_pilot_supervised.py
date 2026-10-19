"""Keep crash recovery outside the process which loads CARLA and SDL."""

from __future__ import annotations

import codecs
import io
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

from src.p8.crash_recovery import REGISTRATION_ENV, TOKEN_ENV, TERMINAL, atomic_json, recover_stopped_run

ROOT = Path(__file__).resolve().parents[1]


def notify_operator(message):
    print(message, flush=True)
    if os.name == "nt":
        import ctypes
        # Native Windows UI: does not import CARLA/pygame or acquire wheel input.
        ctypes.windll.user32.MessageBoxW(
            None, message + "\n\n확인 또는 스페이스바를 누르면 안내창을 닫습니다.",
            "CARLA 실험 진행자 확인", 0x00010000 | 0x00001000 | 0x30)
    elif sys.stdin.isatty():
        input("Press Enter to acknowledge.")


def _tee(pipe, log, errors):
    decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")("replace"), True)
    disk_ok = True
    try:
        while block := os.read(pipe.fileno(), 4096):
            if disk_ok:
                try:
                    log.write(block)
                    log.flush()
                except (OSError, ValueError) as error:
                    disk_ok = False
                    errors.append(f"CONSOLE_LOG:{error}")
            try:
                # Chunks, not lines: the M2 input prompt has no trailing newline.
                sys.stdout.write(decoder.decode(block))
                sys.stdout.flush()
            except (OSError, UnicodeError, ValueError):
                pass  # A detached console must never block the worker's pipe.
    except OSError as error:
        errors.append(f"CONSOLE_PIPE:{error}")


def require_registered_worker_exit(pid):
    """Windows venv python.exe is a redirector: its child's PID is different."""
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        raise ValueError("INVALID_REGISTERED_WORKER_PID")
    if os.name != "nt":
        return  # Popen waits directly for the interpreter on POSIX.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        if ctypes.get_last_error() == 87:  # PID no longer exists
            return
        raise OSError("Cannot confirm registered worker exit")
    try:
        if kernel.WaitForSingleObject(handle, 0) != 0:
            raise RuntimeError("REGISTERED_WORKER_STILL_RUNNING")
    finally:
        kernel.CloseHandle(handle)


def supervise(command, *, output_root, cwd, notice=notify_operator):
    output_root = Path(output_root).resolve()
    diagnostic = output_root / "_supervisor" / uuid4().hex
    diagnostic.mkdir(parents=True)
    registration = diagnostic / "run.json"
    token = uuid4().hex
    env = os.environ.copy()
    env.update({REGISTRATION_ENV: str(registration), TOKEN_ENV: token, "PYTHONIOENCODING": "utf-8",
                "PYTHONFAULTHANDLER": "1"})
    print(f"실행 진단 로그: {diagnostic / 'console.log'}", flush=True)
    errors = []
    with (diagnostic / "console.log").open("wb") as log:
        worker = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, stdin=None)
        pump = threading.Thread(target=_tee, args=(worker.stdout, log, errors), daemon=True)
        pump.start()
        # Ctrl+C reaches the child as before. The parent remains to save evidence.
        old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            code = worker.wait()
        finally:
            signal.signal(signal.SIGINT, old_handler)
        pump.join(timeout=5)
        if pump.is_alive():
            errors.append("CONSOLE_PIPE_DRAIN_TIMEOUT")
        else:
            worker.stdout.close()
    try:
        atomic_json(diagnostic / "exit.json", {"worker_pid": worker.pid, "exit_code": code,
                    "exit_code_hex": f"0x{code & 0xffffffff:08X}", "logging_errors": errors})
    except OSError as error:
        errors.append(f"EXIT_LOG:{error}")
        print(f"종료 진단 저장 오류: {error}", flush=True)
    directory = None
    try:
        if registration.exists():
            record = json.loads(registration.read_text(encoding="utf-8"))
            directory = Path(record["directory"]).resolve()
            if (record.get("launch_token") != token or directory.parent != output_root
                    or directory.name != record["run_id"] or directory.name.startswith(".")):
                raise ValueError("SUPERVISOR_REGISTRATION_MISMATCH")
            require_registered_worker_exit(record["worker_pid"])
            manifest_path = directory / "session_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
            if manifest.get("state") not in TERMINAL:
                print("실험 프로세스 중단을 감지했습니다. 저장된 부분 기록을 복구합니다.", flush=True)
                result = recover_stopped_run(directory, returncode=code, worker_exited=True,
                                             diagnostics=diagnostic)
                print(json.dumps({"status": "CRASH_RECOVERY_SAVED" if result.error is None else "CRASH_RECOVERY_EXPORT_FAILED",
                                  "session_state": "ABORTED", "results_directory": str(directory),
                                  "results_archive": str(result.archive) if result.archive else None,
                                  "error": result.error}), flush=True)
                message = ("실험 프로그램 오류로 주행이 중단되었습니다.\n"
                           "중단 전 저장된 데이터는 부분 기록으로 보존했습니다.\n"
                           "실험이 정상 완료된 것은 아닙니다.\n\n"
                           f"종료 코드: 0x{code & 0xffffffff:08X}\n저장 위치: {directory}\n"
                           + (f"결과 ZIP: {result.archive}" if result.error is None
                              else f"ZIP 저장 실패: {result.error}\n원본 파일은 위 폴더에 남아 있습니다.")
                           + "\n\n진행자는 언리얼의 Play를 멈추고 상태를 확인해 주세요.")
                notice(message)
                return code or 2
            # A crash during cleanup/export can leave a terminal manifest without a ZIP.
            archive = output_root / "_exports" / f"{directory.name}.zip"
            if not archive.exists():
                result = recover_stopped_run(directory, returncode=code, worker_exited=True)
                notice(f"종료 후 결과 저장을 다시 확인했습니다.\n기록: {directory}\n"
                       f"ZIP: {result.archive}\n저장 오류: {result.error or '없음'}")
                return code or (2 if result.error else 0)
        if code not in (0, 2, 130):
            notice(f"실험 프로그램이 비정상 종료되었습니다.\n종료 코드: 0x{code & 0xffffffff:08X}\n"
                   f"진단 로그: {diagnostic}\n진행자가 실험 상태를 확인해 주세요.")
        return code
    except Exception as error:
        # Even failed recovery/storage must reach the operator; never launch another drive.
        notice(f"실험 프로그램이 중단되었고 자동 복구를 완료하지 못했습니다.\n"
               f"오류: {type(error).__name__}:{error}\n원본 기록: {directory}\n진단: {diagnostic}")
        return code or 2


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    code = supervise([sys.executable, "-B", "-u", "-m", "tools.run_pilot_preserve_forces", *argv],
                     output_root=ROOT / "data/p8_operator", cwd=ROOT)
    # Python's Windows exit() expects a signed C long. Preserve native status bits.
    return code - 0x100000000 if os.name == "nt" and code > 0x7fffffff else code


if __name__ == "__main__":
    raise SystemExit(main())
