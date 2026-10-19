"""Exercise the compiled RPC server on a private loopback port, never CARLA."""

import argparse
import concurrent.futures
import json
import socket
import subprocess
import tempfile
import time
from pathlib import Path


def request(name="ping", args=b"\x90", kind=0):
    text = name.encode()
    return (b"\x94\x00\x01" if kind == 0 else b"\x93\x02") + bytes([0xa0 + len(text)]) + text + args


def verify(executable):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    with tempfile.TemporaryFile() as errors, subprocess.Popen(
        [str(executable), str(port)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=errors,
    ) as server:
        try:
            assert server.stdout.readline() == b"READY\r\n"

            def connect():
                return socket.create_connection(("127.0.0.1", port), timeout=3)

            def ping(sock=None):
                if sock is None:
                    with connect() as fresh:
                        return ping(fresh)
                sock.sendall(request())
                received = b""
                while len(received) < 5:
                    block = sock.recv(5 - len(received))
                    assert block, "Healthy RPC connection was closed"
                    received += block
                assert received == b"\x94\x01\x01\xc0\x2a", received

            bad_packets = [
                b"\xc1",  # reserved MessagePack marker: the observed parse_error path
                b"HTTP/1.0\r\n", b"\xc0", b"\x01", b"\xa3abc",
                b"\x90", b"\x94\x01\x01\xa4ping\x90",
                b"\x94\x00\xa1x\xa4ping\x90",
                b"\x94\x00\x01\x01\x90",
                b"\x94\x00\x01\xa4ping\xc0",
                request("echo", b"\x91\xa3bad"), request("fail"),
            ]

            def reject(packet):
                with connect() as bad:
                    bad.sendall(packet)
                    try:
                        assert bad.recv(1) == b"", "Bad connection was not rejected"
                    except ConnectionResetError:
                        pass
                ping()

            with connect() as healthy:
                ping(healthy)
                for packet in bad_packets:
                    reject(packet)
                    ping(healthy)  # same healthy client survives every rejection
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(reject, bad_packets * 5))
                ping(healthy)
            with connect() as fragmented:
                for byte in request():
                    fragmented.sendall(bytes([byte]))
                    time.sleep(0.002)
                assert fragmented.recv(5) == b"\x94\x01\x01\xc0\x2a"
            with connect() as truncated:
                truncated.sendall(b"\x94\x00\x01\xa4pi")
            with connect() as notify:
                notify.sendall(request("note", kind=2))
                for _ in range(100):
                    notify.sendall(request("count"))
                    response = notify.recv(5)
                    if response == b"\x94\x01\x01\xc0\x01":
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError("Valid notification was not delivered")
            ping()
            assert server.poll() is None, "Native server died"
            server.stdin.write(b"STOP\n")
            server.stdin.flush()
            assert server.wait(timeout=10) == 0
            errors.seek(0)
            diagnostics = errors.read().decode()
            for reason in ("MSGPACK_UNPACK_ERROR", "INVALID_RPC_ENVELOPE",
                           "RPC_ARGUMENT_TYPE", "RPC_DISPATCH_EXCEPTION"):
                assert reason in diagnostics, reason
            report = {"status": "PASS", "rejected_connections": len(bad_packets) * 6,
                      "healthy_connection_preserved": True,
                      "fragmentation_notification_disconnect": "PASS"}
            executable.with_suffix(".verification.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8")
            executable.with_suffix(".rejections.jsonl").write_text(diagnostics, encoding="utf-8")
            print(json.dumps(report), flush=True)
        finally:
            if server.poll() is None:
                server.kill()
                server.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, type=Path)
    verify(parser.parse_args().server)
