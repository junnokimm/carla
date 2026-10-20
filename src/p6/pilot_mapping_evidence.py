"""Self-contained coordinate definitions for offline interpretation of gaze.jsonl."""

import hashlib
import json
from dataclasses import asdict

from src.p6.validation.mapping import surface_viewport


def aoi_schema():
    return {
        "version": "pilot-aoi-evidence-v1",
        "aoi_labels": {
            "FORWARD": "전방 (pilot-render-layout-v2-notice-forward부터 하단 중앙 안내문구 영역 포함)",
            "LEFT_MIRROR": "좌측 사이드미러",
            "RIGHT_MIRROR": "우측 사이드미러", "TABLET": "외부 태블릿",
            "REAR_MIRROR": "룸미러 (활성화된 배치에서만 사용)",
            "NO_AOI": "분류 불가 또는 지정 영역 밖",
        },
        "coordinates": {
            "sample.left/right.x/y": "Tobii display plane: top-left=(0,0), center=(0.5,0.5), x right, y down",
            "screen_gaze_display_normalized": "Mean of display-valid eyes; same Tobii display plane",
            "render_point": "Logical driving canvas pixels: top-left=(0,0), x right, y down",
            "geometry": "Windows physical desktop pixels; surface_size is SDL rendering pixels",
            "tablet_profile.polygons": "Per-eye extended Tobii display plane, may lie outside [0,1]; NOT tablet pixels",
        },
        "mapping_join": "gaze.mapping_id -> mapping.mapping_id with definition in this same file",
        "tablet_join": "gaze.tablet_profile_sha256 -> tablet_profile.sha256 in this same file",
        "screen_region_rule": "Logical rectangles use inclusive left/top, exclusive right/bottom; polygon ray crossing",
        "screen_precedence": ["LEFT_MIRROR", "RIGHT_MIRROR", "REAR_MIRROR", "FORWARD"],
        "tablet_rule": "Require live serial/display-area binding and per-eye hull agreement; screen conflict is uncertain",
        "observation_states": {
            "FORWARD/LEFT_MIRROR/RIGHT_MIRROR/REAR_MIRROR": "Classified screen region",
            "TABLET_BINOCULAR/TABLET_MONOCULAR": "Classified external tablet, two/one tracked eyes",
            "TRACKING_LOST": "No SDK-tracked eye; never infer tablet",
            "MAPPING_UNAVAILABLE": "Tracked eyes but window/display mapping unusable",
            "VALID_UNMAPPED": "Tracked but no configured region matched",
            "AOI_UNCERTAIN": "Screen/tablet conflict or per-eye tablet disagreement",
        },
        "analysis_rule": "Use aoi with observation_state and analysis_aoi_included; null/false is not an analysis pass",
        "limitations": "Geometry mapping does not establish participant calibration accuracy or recover tracking loss",
    }


def mapping_evidence(geometry, layout):
    """Record forward and inverse transform using the classifier's viewport."""
    lw, lh = layout.mapping.render_size
    sw, sh = geometry.surface_size
    viewport = surface_viewport((sw, sh), (lw, lh))
    monitor, client = geometry.monitor, geometry.client
    # normalized_display = offset + logical_pixel * scale
    offset = [
        (client.x - monitor.x + viewport.x * client.width / sw) / monitor.width,
        (client.y - monitor.y + viewport.y * client.height / sh) / monitor.height,
    ]
    scale = [viewport.width * client.width / sw / lw / monitor.width,
             viewport.height * client.height / sh / lh / monitor.height]
    regions = {"FORWARD": {"polygon": [[p.x, p.y] for p in layout.forward.points]}}
    for name, rect in (("LEFT_MIRROR", layout.left), ("RIGHT_MIRROR", layout.right),
                       ("REAR_MIRROR", layout.rear)):
        if name != "REAR_MIRROR" or layout.rear_available:
            regions[name] = {"rect": asdict(rect)}
    definition = {
        "geometry": asdict(geometry), "aoi_version": layout.version,
        "logical_size": [lw, lh], "surface_viewport": asdict(viewport),
        "logical_to_display_normalized": {"offset": offset, "scale": scale},
        "screen_regions_logical": regions, "rear_available": layout.rear_available,
        "tablet_region": "See tablet_profile record; independent extended display-plane per-eye hulls",
    }
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return "mapping-" + digest[:24], definition
