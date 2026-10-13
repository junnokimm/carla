from dataclasses import astuple, replace
from pathlib import Path

from src.p6.aoi import Aoi, Point
from src.p6.aoi_config import load_aoi_layout
from src.p6.validation.mapping import candidate_aoi
from src.scenario.mirror_layout import MirrorLayoutSpec, calculate_mirror_layout


def test_candidate_matches_current_mirrors_without_claiming_measured_accuracy() -> None:
    # Given the approved +480 layout and the unmeasured candidate configuration.
    layout = calculate_mirror_layout(
        MirrorLayoutSpec((1280, 720), (240, 135), (320, 90), 24, 100 / 720)
    )
    candidate = load_aoi_layout(
        Path(__file__).resolve().parents[1] / "config/p6_aoi_candidate.json"
    )

    # When checking geometry independently of physical calibration.
    report = candidate.validate()

    # Then all mirror rectangles agree, but classification remains unapproved.
    assert astuple(candidate.rear) == layout.rear_mirror.bounds == (960, 100, 320, 90)
    assert astuple(candidate.left) == layout.left_mirror.bounds
    assert astuple(candidate.right) == layout.right_mirror.bounds
    assert candidate.rear.x + candidate.rear.width == 1280
    assert report.issues == ()
    assert not report.valid
    assert report.tablet_status == "NOT_TESTED"
    assert candidate.classify(0.5, 0.5) is Aoi.INVALID_LAYOUT


def test_rear_disabled_candidate_keeps_region_unavailable_without_reassignment() -> None:
    candidate = load_aoi_layout(
        Path(__file__).resolve().parents[1]
        / "config/p6_aoi_rear_disabled_candidate_v1.json"
    )
    verified = replace(
        candidate,
        mapping=replace(
            candidate.mapping,
            display_mapping_verified=True,
            client_mapping_verified=True,
        ),
    )

    assert candidate.version == "p6-rear-disabled-candidate-unvalidated-1"
    assert candidate.rear_available is False
    assert verified.classify(0.875, 0.2) is Aoi.NO_AOI
    assert candidate_aoi(Point(1120, 144), candidate) == "NO_AOI"
    assert verified.classify(0.1, 0.85) is Aoi.LEFT_MIRROR
    assert verified.classify(0.9, 0.85) is Aoi.RIGHT_MIRROR
