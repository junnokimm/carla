"""Relink the verified CARLA client with a narrow lane-change localization fix.

All outputs stay in this project. The installed Python environments are read-only.
Requires the existing pinned native build and VS 2022 x64 tools; no server is used.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tools.build_rpc_guard import build_guard

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(r"C:\dev\carla-native-tm-cancel-20261012")
WORK = ROOT / "data/noa_tm_build"
OUTPUT = ROOT / "native/noa_tm"
PINNED = {
    "install/lib/carla_client.lib": "863522ca6acbebbad8ce4f283a830c415df0a92e0c420d3c3740cedf447bea7b",
    "install/include/carla/trafficmanager/LocalizationStage.cpp": "ccab6fdc945054bd6633bc57254aafa472c30b0c4a92612132bc4493c6fbf549",
}


def patch_source(source: str) -> str:
    replacements = [
        (
            "&& (recently_not_executed_lane_change || done_with_previous_lane_change))",
            (
                "// Explicit requests are serialized by the application until completion.\n"
                "      // The random-lane-change cooldown must not swallow the next request.\n"
                "      && (lane_change_info.change_lane || recently_not_executed_lane_change || done_with_previous_lane_change))"
            ),
        ),
        (
            (
                "      const SimpleWaypointPtr starting_point = change_over_point;\n"
                "      while (change_over_point->DistanceSquared(starting_point) < SQUARE(change_over_distance) &&\n"
                "             !change_over_point->CheckJunction()) {\n"
                "        change_over_point = change_over_point->GetNextWaypoint().front();\n"
                "      }"
            ),
            (
                "      const SimpleWaypointPtr starting_point = change_over_point;\n"
                "      // Update() clears a buffer whose first point is > MAX_START_DISTANCE\n"
                "      // from the ego. Measure forced targets from the EGO, not only from\n"
                "      // the adjacent waypoint, so a new plan survives the next cycle.\n"
                "      if (force && cg::Math::DistanceSquared(change_over_point->GetLocation(),\n"
                "                                             vehicle_location) > SQUARE(MAX_START_DISTANCE)) {\n"
                "        return nullptr;\n"
                "      }\n"
                "      while (change_over_point->DistanceSquared(starting_point) < SQUARE(change_over_distance) &&\n"
                "             !change_over_point->CheckJunction()) {\n"
                "        const auto next = change_over_point->GetNextWaypoint();\n"
                "        if (next.empty() || next.front() == change_over_point) break;\n"
                "        if (force && cg::Math::DistanceSquared(next.front()->GetLocation(),\n"
                "                                               vehicle_location) > SQUARE(MAX_START_DISTANCE)) break;\n"
                "        change_over_point = next.front();\n"
                "      }"
            ),
        ),
    ]
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError("Pinned LocalizationStage does not match patch")
        source = source.replace(old, new, 1)
    return source


def build_environment() -> dict[str, str]:
    vcvars = Path(
        r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
    )
    result = subprocess.run(
        f'cmd.exe /d /s /c ""{vcvars}" >nul && set"',
        check=True,
        capture_output=True,
        text=True,
    )
    env = os.environ.copy()
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            env[key] = value
    return env


def main() -> None:
    for name, digest in PINNED.items():
        if hashlib.sha256((BASE / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Native input hash mismatch: {name}")
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    env = build_environment()
    compiler = str(Path(env["VCToolsInstallDir"]) / "bin/Hostx64/x64/cl.exe")
    linker = str(Path(env["VCToolsInstallDir"]) / "bin/Hostx64/x64/link.exe")
    rpc_obj, rpc_manifest = build_guard(ROOT, BASE, WORK, env, compiler, linker)
    subprocess.run([sys.executable, "-B", "-m", "tools.verify_rpc_guard",
                    "--server", str(WORK / "rpc_guard_server.exe")],
                   cwd=ROOT, check=True)
    include = BASE / "install/include"
    original = (include / "carla/trafficmanager/LocalizationStage.cpp").read_text()
    patched = patch_source(original)
    source = WORK / "LocalizationStage.cpp"
    source.write_text(patched, encoding="utf8", newline="\n")
    (OUTPUT / "lane_change.patch").write_text(
        "".join(
            difflib.unified_diff(
                original.splitlines(True),
                patched.splitlines(True),
                fromfile="a/LocalizationStage.cpp",
                tofile="b/LocalizationStage.cpp",
            )
        ),
        encoding="utf8",
        newline="\n",
    )
    flags = [
        "/nologo",
        "/O2",
        "/MD",
        "/EHsc",
        "/std:c++14",
        "/DNDEBUG",
        "/D_WIN32_WINNT=0x0600",
        "/DHAVE_SNPRINTF",
        "/DBOOST_ERROR_CODE_HEADER_ONLY",
        "/DLIBCARLA_IMAGE_WITH_PNG_SUPPORT",
        f"/I{include}",
        f"/I{BASE / 'source/LibCarla/source/third-party'}",
    ]
    for dep in (
        "boost-1.84.0-install",
        "rpclib-install",
        "recast-install",
        "libpng-1.2.37-install",
    ):
        flags.append(f"/I{BASE / 'Build' / dep / 'include'}")
    obj = WORK / "LocalizationStage.obj"
    subprocess.run(
        [compiler, *flags, "/c", str(source), f"/Fo{obj}"],
        env=env,
        cwd=WORK,
        check=True,
    )
    deps = BASE / "source/PythonAPI/carla/dependencies/lib"
    libraries = [str(BASE / "install/lib/carla_client.lib")]
    libraries += [
        str(deps / name)
        for name in (
            "Detour.lib",
            "DetourCrowd.lib",
            "libboost_filesystem-vc143-mt-x64-1_84.lib",
            "libboost_python312-vc143-mt-x64-1_84.lib",
            "libpng.lib",
            "osm2odr.lib",
            "proj.lib",
            "Recast.lib",
            "rpc.lib",
            "sqlite3.lib",
            "xerces-c_3.lib",
            "zlib.lib",
        )
    ]
    system_libs = ["shlwapi.lib", "Advapi32.lib", "ole32.lib", "shell32.lib"]
    test_obj = WORK / "noa_tm_localization.obj"
    subprocess.run(
        [
            compiler,
            *flags,
            "/c",
            str(ROOT / "tests/native/noa_tm_localization.cpp"),
            f"/Fo{test_obj}",
        ],
        env=env,
        cwd=WORK,
        check=True,
    )
    # Same test first runs against the unmodified production library (negative control).
    for name, objects in (("baseline", [test_obj]), ("patched", [test_obj, obj])):
        exe = WORK / f"localization_{name}.exe"
        subprocess.run(
            [
                linker,
                "/nologo",
                *map(str, objects),
                *libraries,
                *system_libs,
                f"/OUT:{exe}",
            ],
            env=env,
            cwd=WORK,
            check=True,
        )
        run = subprocess.run(
            [str(exe), r"C:\CarlaUE4\Content\Carla\Maps\OpenDrive\Town04.xodr"],
            capture_output=True,
            text=True,
            cwd=WORK,
            check=False,
        )
        (WORK / f"{name}_test.txt").write_text(run.stdout + run.stderr, encoding="utf8")
        print(name, run.returncode, run.stdout, run.stderr, flush=True)
        if (name == "patched" and run.returncode != 0) or (
            name == "baseline" and run.returncode == 0
        ):
            raise RuntimeError(f"Native regression/negative-control failed: {name}")
    package = OUTPUT / "carla"
    package.mkdir(exist_ok=True)
    # The old build/temp object was subsequently rebuilt with diagnostics. Build
    # the bindings against the pinned NORMAL headers instead of mixing ABIs.
    bindings = WORK / "bindings"
    shutil.copytree(
        BASE / "source/PythonAPI/carla/source/libcarla", bindings, dirs_exist_ok=True
    )
    tm_binding = bindings / "TrafficManager.cpp"
    binding_source = tm_binding.read_text()
    start = binding_source.index("boost::python::dict InterReadDiagnosticTrace(")
    end = binding_source.index("void export_trafficmanager()", start)
    binding_source = binding_source[:start] + binding_source[end:]
    start = binding_source.index('    .def("enable_diagnostic_trace"')
    end = binding_source.index('    .def("shut_down"', start)
    tm_binding.write_text(
        binding_source[:start] + binding_source[end:], encoding="utf8"
    )
    binding_obj = WORK / "libcarla.obj"
    subprocess.run(
        [
            compiler,
            *flags,
            "/GL",
            "/bigobj",
            "/DBOOST_ALL_NO_LIB",
            "/DBOOST_PYTHON_STATIC_LIB",
            "/DLIBCARLA_WITH_PYTHON_SUPPORT",
            f"/I{Path(sys.base_prefix) / 'include'}",
            f"/I{BASE / 'source/PythonAPI/carla/dependencies/include'}",
            f"/I{BASE / 'source/PythonAPI/carla/dependencies/include/system'}",
            "/c",
            str(bindings / "libcarla.cpp"),
            f"/Fo{binding_obj}",
        ],
        env=env,
        cwd=WORK,
        check=True,
    )
    pyd = package / "libcarla.cp312-win_amd64.pyd"
    subprocess.run(
        [
            linker,
            "/nologo",
            "/INCREMENTAL:NO",
            "/LTCG",
            "/DLL",
            "/MANIFEST:EMBED,ID=2",
            "/MANIFESTUAC:NO",
            "/EXPORT:PyInit_libcarla",
            str(binding_obj),
            str(obj),
            str(rpc_obj),
            *libraries,
            *system_libs,
            r"/LIBPATH:C:\Users\nexta\AppData\Local\Programs\Python\Python312\libs",
            f"/OUT:{pyd}",
            f"/IMPLIB:{WORK / 'libcarla.lib'}",
            f"/MAP:{WORK / 'libcarla.map'}",
            "/DEBUG:FULL",
            "/OPT:REF,ICF",
            f"/PDB:{WORK / 'libcarla.pdb'}",
        ],
        env=env,
        cwd=WORK,
        check=True,
    )
    shutil.copyfile(
        BASE / ".venv-p8/Lib/site-packages/carla/__init__.py", package / "__init__.py"
    )
    manifest = {
        "version": "p8-noa-lane-change-rpc-guard-v2",
        "rpc_guard": rpc_manifest,
        "base_inputs": PINNED,
        "libcarla_sha256": hashlib.sha256(pyd.read_bytes()).hexdigest(),
        "patch_sha256": hashlib.sha256(
            (OUTPUT / "lane_change.patch").read_bytes()
        ).hexdigest(),
    }
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf8"
    )
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
