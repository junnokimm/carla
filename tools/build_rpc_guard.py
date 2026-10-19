"""Build the RPC receive guard against the pinned CARLA rpclib ABI."""

import difflib
import hashlib
import subprocess
from pathlib import Path

RPC_SOURCE = Path(r"D:\CARLA_0.9.16_Source\Build\rpclib-src")
RPC_LIBRARY_SHA256 = "f78f889a616baed0b339aca96573b0891b6e05a8cb6612c16ba5c8dac39bab79"
SOURCE_SHA256 = "bd3c02338cf1e722bcf9a51eb1d70a4ad22923a6187d284b8db14fcfe9a29ff2"
HEADER_HASHES = {
    "rpc/detail/server_session.h": "98dfc6a5feb7528492c417b7d073879b59a7a2751ef910349bd2f143802793df",
    "rpc/detail/async_writer.h": "bfea5eb79ceef0cf73d094cbb874cc76081715b47d5554f31931a2671bd9719a",
}

HELPERS = r'''
// No control or simulation policy belongs here. Reject only the broken RPC
// connection; keep the server workers and all other clients running.
static bool valid_request(const RPCLIB_MSGPACK::object &msg) {
    using namespace RPCLIB_MSGPACK;
    if (msg.type != type::ARRAY) return false;
    const auto n = msg.via.array.size;
    if (n != 3 && n != 4) return false;
    const auto *a = msg.via.array.ptr;
    if (a[0].type != type::POSITIVE_INTEGER || a[0].via.u64 != (n == 4 ? 0 : 2))
        return false;
    if (n == 4 && (a[1].type != type::POSITIVE_INTEGER ||
                  a[1].via.u64 > UINT32_MAX)) return false;
    return a[n - 2].type == type::STR && a[n - 1].type == type::ARRAY;
}

static void report_rejection(const char *reason, RPCLIB_ASIO::ip::tcp::socket &socket) {
    std::error_code ec;
    const auto peer = socket.remote_endpoint(ec);
    const unsigned port = ec ? 0 : peer.port();
    const unsigned long address = !ec && peer.address().is_v4()
        ? peer.address().to_v4().to_ulong() : 0;
    // Static reason strings and numeric peer only: never log incoming payloads.
    std::fprintf(stderr, "{\"status\":\"NATIVE_RPC_REJECTED\",\"reason\":\"%s\","
                 "\"peer_ipv4\":\"%lu.%lu.%lu.%lu\",\"peer_port\":%u}\n",
                 reason, (address >> 24) & 255, (address >> 16) & 255,
                 (address >> 8) & 255, address & 255, port);
    std::fflush(stderr);
}
'''


def patch_source(source):
    replacements = [
        ('#include "rpc/detail/log.h"', '#include "rpc/detail/log.h"\n#include <cstdio>'),
        ("static constexpr std::size_t default_buffer_size =", HELPERS + "\nstatic constexpr std::size_t default_buffer_size ="),
        ("        socket_.close();", "        std::error_code ignored;\n        socket_.close(ignored);"),
        ("            if (!ec) {\n                pac_", "            if (!ec) {\n              try {\n                pac_"),
        ("                    auto msg = result.get();", """                    auto msg = result.get();
                    if (!valid_request(msg)) {
                        report_rejection("INVALID_RPC_ENVELOPE", socket_);
                        self->close();
                        return;
                    }"""),
        ("                    io_->post([this, self, msg, z]() {", """                    io_->post([this, self, msg, z]() {
                      if (exit_) return;
                      try {"""),
        ("                    });\n                }", """                      } catch (const RPCLIB_MSGPACK::type_error &) {
                        report_rejection("RPC_ARGUMENT_TYPE", socket_);
                        self->close();
                      } catch (const std::exception &) {
                        // A handler exception may escape when suppression is off.
                        // Do not report successful execution or retry the command.
                        report_rejection("RPC_DISPATCH_EXCEPTION", socket_);
                        self->close();
                      }
                    });
                }"""),
        ("            } else if (ec == RPCLIB_ASIO::error::eof ||", """              } catch (const RPCLIB_MSGPACK::unpack_error &) {
                report_rejection("MSGPACK_UNPACK_ERROR", socket_);
                self->close();
              }
            } else if (ec == RPCLIB_ASIO::error::eof ||"""),
        ("[this]() { exit_ = true; }", "[this, self]() { exit_ = true; }"),
        ("[this]() { parent_->close_sessions(); }", "[this, self]() { parent_->close_sessions(); }"),
    ]
    for before, after in replacements:
        expected = 2 if before == "        socket_.close();" else 1
        if source.count(before) != expected:
            raise ValueError(f"RPC patch context mismatch: {before}")
        source = source.replace(before, after)
    return source


def build_guard(root, base, work, env, compiler, linker):
    vendor = root / "native/noa_tm/rpc_guard"
    upstream = vendor / "server_session.upstream.cc"
    library = base / "source/PythonAPI/carla/dependencies/lib/rpc.lib"
    if hashlib.sha256(upstream.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("RPC upstream source hash mismatch")
    if hashlib.sha256(library.read_bytes()).hexdigest() != RPC_LIBRARY_SHA256:
        raise ValueError("RPC library hash mismatch")
    include = base / "Build/rpclib-install/include"
    for name, expected in HEADER_HASHES.items():
        if hashlib.sha256((include / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"RPC ABI header mismatch: {name}")
    dep = RPC_SOURCE / "dependencies/include"
    if hashlib.sha256((dep / "asio.hpp").read_bytes()).hexdigest() != "276a3f4997e222b139ed6804e7bfd20ef9708a26e4b3d1b68c6db680d404ab07":
        raise ValueError("RPC Asio header mismatch")
    tree = hashlib.sha256()
    for path in sorted((dep / "asio").rglob("*")):
        if path.is_file():
            tree.update(path.relative_to(dep).as_posix().encode())
            tree.update(path.read_bytes())
    if tree.hexdigest() != "f2aa4ec4c7244f3dc9bc85488145bec7f605b1c4563ed91c13dcbcb1277d76a1":
        raise ValueError("RPC Asio dependency tree mismatch")
    original = upstream.read_text()
    patched = patch_source(original)
    (vendor / "receive_guard.patch").write_text("".join(difflib.unified_diff(
        original.splitlines(True), patched.splitlines(True),
        fromfile="a/server_session.cc", tofile="b/server_session.cc")), encoding="utf-8")
    source = work / "server_session_guard.cc"
    source.write_text(patched, encoding="utf-8")
    flags = ["/nologo", "/O2", "/MD", "/EHsc", "/std:c++14", "/DNDEBUG", "/Z7",
             "/D_WIN32_WINNT=0x0600", "/DASIO_STANDALONE", "/DRPCLIB_WIN32",
             "/DRPCLIB_ASIO=clmdep_asio", "/DRPCLIB_FMT=clmdep_fmt",
             f"/I{include}", f"/I{dep}"]
    obj = work / "server_session_guard.obj"
    subprocess.run([compiler, *flags, "/c", str(source), f"/Fo{obj}"],
                   cwd=work, env=env, check=True)
    test_obj = work / "rpc_guard_server.obj"
    subprocess.run([compiler, *flags, "/c", str(root / "tests/native/rpc_guard_server.cpp"),
                    f"/Fo{test_obj}"], cwd=work, env=env, check=True)
    exe = work / "rpc_guard_server.exe"
    subprocess.run([linker, "/nologo", str(test_obj), str(obj), str(library),
                    "ws2_32.lib", f"/OUT:{exe}"], cwd=work, env=env, check=True)
    return obj, {"rpc_library_sha256": RPC_LIBRARY_SHA256,
                 "upstream_sha256": SOURCE_SHA256,
                 "patched_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                 "abi_header_sha256": HEADER_HASHES}
