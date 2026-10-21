import csv
import io
import json
import time
from dataclasses import replace
from zipfile import ZipFile

import pygame
import pytest

from src.experiment.context import Module1Condition, Module2Condition
from src.p6.aoi import Point, Rect
from src.p6.aoi_config import load_aoi_layout
from src.p6.external_tablet import ExternalTablet
from src.p6.pilot_aoi import classify_screen, layout_for_view, layout_record
from src.p6.pilot_mapping_evidence import mapping_evidence
from src.p8.model import Stage
from src.p8.operator_cli import build_parser
from src.p8.operator_model import DevOutcome
from src.p8.pilot_gaze import PilotGaze
from src.scenario.driver_view import DriverViewConfig
from src.scenario.driver_view_presentation import aspect_fit_rect
from src.scenario.research_exit_hud import ExitNavigationRenderer
from tests.test_clear_road_hazard import expose, setup
from tests.test_external_tablet_gaze import decoded, display_record, setup_collector
from tests.test_p5_driver_view_binding import recommendation
from tests.test_p8_operator_backend import StrictClock, advance_to, register_dev
from tests.test_p8_pilot_launcher import settings
from tests.test_pilot_gaze import Probe, Source
from tests.test_scaled_pilot_mapping import four_k
from tools import run_pilot


@pytest.mark.parametrize("m1", ["1", "2", "SURT", "NO_SURT"])
@pytest.mark.parametrize("m2", ["1", "2"])
def test_explicit_assignments_bypass_prompts_and_leave_config_unchanged(tmp_path, monkeypatch, m1, m2):
    path = tmp_path / "pilot.json"
    original = json.dumps(settings())
    path.write_text(original, encoding="utf-8")
    received = []
    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    monkeypatch.setattr(run_pilot.operator_cli, "main",
                        lambda argv: received.append(build_parser().parse_args(argv)) or 0)
    assert run_pilot.main(["--config", str(path), "--participant-id", "001", "--session-type", "test", "--m1", m1, "--m2", m2]) == 0
    assert received[0].m1 == run_pilot.M1_CHOICES.get(m1, m1)
    assert received[0].m2 == run_pilot.M2_CHOICES[m2]
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("error,code", [(EOFError, 2), (KeyboardInterrupt, 130)])
def test_cancel_m2_after_selecting_m1_never_starts_experiment(tmp_path, monkeypatch, error, code):
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(settings()), encoding="utf-8")
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "1"
        raise error
    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(run_pilot.operator_cli, "main", lambda _: pytest.fail("launched"))
    assert run_pilot.main(["--config", str(path), "--participant-id", "001", "--session-type", "test"]) == code
    assert len(prompts) == 2


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080), (3840, 2160)])
def test_actual_rendered_notices_fit_forward_without_capturing_mirrors(tmp_path, size):
    config = DriverViewConfig(window_size=size)
    layout = layout_for_view(config)
    assert not layout.validate().issues
    pygame.font.init()
    class Recorder:
        def __init__(self):
            self.rects = []
        def get_width(self): return size[0]
        def get_height(self): return size[1]
        def blit(self, image, dest):
            self.rects.append(image.get_rect(topleft=dest.topleft if isinstance(dest, pygame.Rect) else dest))
    for mode in ("MANUAL", "NOA_ACTIVE"):
        scene, world, *_ = setup(tmp_path / mode, mode)
        expose(scene, world)
        recorder = Recorder()
        scene.draw_warning(recorder)
        assert scene.warning_drawn and not scene.warning_errors
        renderer = ExitNavigationRenderer(size)
        renderer._draw_right_arrow = lambda _, rect: recorder.rects.append(
            pygame.Rect(rect.right + 12, rect.centery - 8, 22, 18))
        renderer.draw(recorder, recommendation(), "x", "r")
        for rect in recorder.rects:
            for point in (rect.topleft, rect.topright, rect.bottomleft, rect.bottomright, rect.center):
                assert layout.forward.contains(Point(*point)), (mode, size, rect, point)
        scene.finalize("TEST_END")
    for rect in (layout.left, layout.right):
        for x in (rect.x, rect.x + rect.width / 2, rect.x + rect.width - 1):
            for y in (rect.y, rect.y + rect.height / 2, rect.y + rect.height - 1):
                assert not layout.forward.contains(Point(x, y))
    # No blanket enlargement to the lower right or the bottom of the screen.
    assert not layout.forward.contains(Point(size[0] * .85, size[1] * .85))
    assert not layout.forward.contains(Point(size[0] * .5, size[1] * .95))


@pytest.mark.parametrize("surface", [(1280, 720), (1920, 1080), (3840, 2160), (1024, 768)])
@pytest.mark.parametrize("windowed", [False, True])
def test_notice_aoi_uses_same_letterbox_and_moved_window_transform(surface, windowed):
    observed = four_k(surface)
    if windowed:
        observed = replace(observed, client=Rect(2000, 100, 1920, 1080))
    layout = layout_for_view(DriverViewConfig())
    viewport = aspect_fit_rect((1280, 720), surface)
    for expected, point in [("FORWARD", (640, 610)), ("LEFT_MIRROR", (144, 520)),
                            ("RIGHT_MIRROR", (1136, 520)), ("NO_AOI", (1100, 610))]:
        sx = (viewport.x + point[0] * viewport.width / 1280) / surface[0]
        sy = (viewport.y + point[1] * viewport.height / 720) / surface[1]
        nx = (observed.client.x - observed.monitor.x + sx * observed.client.width) / observed.monitor.width
        ny = (observed.client.y - observed.monitor.y + sy * observed.client.height) / observed.monitor.height
        actual, render = classify_screen(nx, ny, observed, layout)
        assert actual == expected
        assert render == pytest.approx(dict(zip(("x", "y"), point)))
        _, definition = mapping_evidence(observed, layout)
        assert definition["screen_regions_logical"]["FORWARD"]["polygon"] == layout_record(layout)["forward_polygon"]


def test_live_tablet_profile_does_not_overlap_notice_extension():
    tablet = ExternalTablet.load("config/p8_external_tablet_20261019_070521.json")
    from src.p6.external_tablet import inside_convex
    # Every point in the newly included region stays outside both empirical hulls.
    for x in range(282, 999, 4):
        for y in range(500, 641, 4):
            assert all(not inside_convex((x / 1280, y / 720), polygon)
                       for polygon in tablet.polygons.values())


def test_sidecar_separates_notice_mirrors_tablet_and_tracking_loss(tmp_path):
    item = setup_collector(tmp_path)
    item._raw(json.dumps(display_record()))
    points = [(.5, .85), (.1125, .72), (.8875, .72), (1.1, 1.2)]
    for i, point in enumerate(points):
        item._sample(decoded(left=point, right=point, index=i))
    item._sample(decoded(valid=(0, 0), index=4))
    item._sample(decoded(left=(1.1, 1.2), right=(.5, .85), index=5))
    rows = [json.loads(line) for line in item.gaze.getvalue().splitlines()]
    rows = [row for row in rows if row["record_type"] == "gaze"]
    assert [row["aoi"] for row in rows] == [
        "FORWARD", "LEFT_MIRROR", "RIGHT_MIRROR", "TABLET", "NO_AOI", "NO_AOI"]
    assert [row["observation_state"] for row in rows[-2:]] == ["TRACKING_LOST", "AOI_UNCERTAIN"]
    assert not item.stop.is_set() and not item.errors


@pytest.mark.parametrize("rear,path", [
    (False, "config/p6_aoi_rear_disabled_candidate_v1.json"),
    (True, "config/p6_aoi_candidate.json"),
])
def test_standalone_validation_config_matches_runtime(rear, path):
    actual = layout_for_view(replace(DriverViewConfig(), rear_mirror_enabled=rear))
    assert load_aoi_layout(path) == actual


@pytest.mark.parametrize("m1", ["1", "2"])
@pytest.mark.parametrize("m2", ["1", "2"])
def test_terminal_assignment_survives_modules_gaze_reports_and_zip(tmp_path, monkeypatch, m1, m2):
    pilot = tmp_path / "pilot.json"
    pilot.write_text(json.dumps(settings()), encoding="utf-8")
    gaze_config = tmp_path / "gaze.json"
    gaze_config.write_text(json.dumps({"tobii_python": "unused", "display_confirmed": True}))
    chosen, prompts = {}, []
    replies = iter(["", "0", m1, "3", m2])
    def answer(prompt):
        prompts.append(prompt)
        return next(replies)
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(run_pilot, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    def execute(argv):
        args = build_parser().parse_args(argv)
        clock = StrictClock()
        controller, evidence = register_dev(tmp_path / "sessions", clock,
                                            Module1Condition(args.m1), Module2Condition(args.m2))
        chosen["controller"] = controller
        services = controller.session.services
        advance_to(controller, Stage.MODULE_1_RUNNING)
        item = PilotGaze(services, Stage.MODULE_1_RUNNING, DriverViewConfig(), gaze_config,
                         source_factory=Source, probe_factory=Probe)
        item.presentation = (time.monotonic_ns(), 1, (1280, 720))
        item.last_present_ns = time.monotonic_ns()
        item.start()
        deadline = time.monotonic() + 3
        while not item.counts["samples"] and time.monotonic() < deadline:
            time.sleep(.01)
        item.close()
        assert item.counts["samples"] > 0 and not item.errors
        controller.execute_current_module()
        controller.record_execution_policy_outcome(DevOutcome.PARTIAL, "offline nominal M1")
        controller.advance()
        controller.advance()
        controller.advance()
        controller.advance()
        assert controller.snapshot().stage is Stage.MODULE_2_RUNNING
        controller.execute_current_module()
        # Nominal M2 already records its handoff and advances to FINAL_HAZARD.
        assert controller.snapshot().stage is Stage.FINAL_HAZARD
        controller.advance()
        controller.record_execution_policy_outcome(DevOutcome.SKIPPED, "external questionnaire")
        controller.advance()
        assert controller.snapshot().stage is Stage.SESSION_COMPLETE
        return 0

    monkeypatch.setattr(run_pilot.operator_cli, "main", execute)
    assert run_pilot.main(["--config", str(pilot), "--participant-id", "001", "--session-type", "test"]) == 0
    assert all("M1" in prompt for prompt in prompts[:3])
    assert all("M2" in prompt for prompt in prompts[3:])
    controller = chosen["controller"]
    exported = controller.snapshot().result_export
    assert exported.error is None
    expected = run_pilot.M1_CHOICES[m1]
    expected_m2 = run_pilot.M2_CHOICES[m2]
    with ZipFile(exported.archive) as archive:
        assert archive.testzip() is None
        base = exported.directory.name + "/"
        def document(name):
            return json.loads(archive.read(base + name))
        manifest = document("session_manifest.json")
        assert manifest["study_context"]["module_1_condition"] == expected
        assert manifest["study_context"]["module_2_condition"] == expected_m2
        reference = document("collection/surt_external_link.json")
        assert reference["m1_surt_assigned"] is (expected == "SURT")
        assert reference["m1_condition"] == expected
        assert reference["actual_app_execution_verified"] is False
        assert reference["app_logs_inspected"] is False
        assert document("collection/collection_status.json")["m1_condition"] == expected
        for filename in ("research_telemetry_operator-run.csv", "research_events_operator-run.csv",
                         "collection/MODULE_1_vehicle.csv", "collection/events_time_aligned.csv"):
            rows = list(csv.DictReader(io.StringIO(archive.read(base + filename).decode("utf-8"))))
            assert rows and all(row["module_1_condition"] == expected for row in rows)
            module_rows = [row for row in rows if row["phase"] == "MODULE_1"]
            assert module_rows and all(row["condition"] == expected for row in module_rows)
        gaze_name = next(name for name in archive.namelist() if name.endswith("/gaze.jsonl"))
        gaze_rows = [json.loads(line) for line in archive.read(gaze_name).splitlines()]
        assert all(row["condition"] == expected for row in gaze_rows
                   if row["record_type"] in ("metadata", "gaze"))
        gaze_summary = json.loads(archive.read(gaze_name.replace("gaze.jsonl", "summary.json")))
        assert gaze_summary["condition"] == expected
        assert gaze_summary["study_conditions"]["module_2_condition"] == expected_m2
