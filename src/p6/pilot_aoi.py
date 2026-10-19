"""AOIs derived from the rendered pilot layout, not stale screen coordinates."""

from dataclasses import asdict

from src.p6.aoi import AoiLayout, DisplayMapping, Point, Polygon, Rect


def layout_for_view(config):
    from src.scenario.driver_view import calculate_layout

    rendered = calculate_layout(config)
    width, height = config.window_size
    left, right, rear = (
        Rect(*rect.bounds)
        for rect in (rendered.left_mirror, rendered.right_mirror, rendered.rear_mirror)
    )
    # Keep the existing road/HUD boundary at 500/720 of the logical canvas.
    # Cut the raised side mirrors out of that region; the rest is unassigned.
    bottom = height * 500 / 720
    # One pixel separates adjacent AOIs, including their polygon boundaries.
    shoulder = min(bottom, left.y, right.y) - 1
    points = [(0, 0), (width, 0)]
    if config.rear_mirror_enabled:
        # The current rear overlay touches the right edge of the 1280px canvas.
        points += [(width, rear.y - 1), (rear.x - 1, rear.y - 1),
                   (rear.x - 1, rear.y + rear.height + 1), (width, rear.y + rear.height + 1)]
    points += [(width, shoulder), (right.x - 1, shoulder), (right.x - 1, bottom),
               (left.x + left.width + 1, bottom), (left.x + left.width + 1, shoulder),
               (0, shoulder)]
    bounds = Rect(0, 0, width, height)
    return AoiLayout(
        "pilot-render-layout-v1", DisplayMapping(bounds, bounds, bounds, False, False),
        Polygon(tuple(Point(*point) for point in points)), rear, left, right,
        None, (), False, rear_available=config.rear_mirror_enabled,
    )


def layout_record(layout):
    rect = lambda value: [value.x, value.y, value.width, value.height]
    return {
        "version": layout.version,
        "desktop_rect": rect(layout.mapping.desktop),
        "display_rect": rect(layout.mapping.display),
        "client_rect": rect(layout.mapping.client),
        "display_mapping_verified": False, "client_mapping_verified": False,
        "forward_polygon": [[p.x, p.y] for p in layout.forward.points],
        "rear_rect": rect(layout.rear), "left_rect": rect(layout.left),
        "right_rect": rect(layout.right), "rear_available": layout.rear_available,
        "tablet_rect": None, "tablet_mapping_verified": False, "other_polygons": [],
    }


def classify_screen(x, y, geometry, layout):
    """Map display-normalized gaze through the same aspect-fit viewport as F11."""
    if layout.validate().issues:
        return "INVALID_LAYOUT", None
    if not (0 <= x < 1 and 0 <= y < 1):
        return "NO_AOI", None
    monitor, client = geometry.monitor, geometry.client
    screen = Point(monitor.x + x * monitor.width, monitor.y + y * monitor.height)
    if not client.contains(screen):
        return "NO_AOI", None
    # Physical client pixels and rendering surface pixels can differ with DPI.
    sw, sh = geometry.surface_size
    px = (screen.x - client.x) * sw / client.width
    py = (screen.y - client.y) * sh / client.height
    lw, lh = layout.mapping.render_size
    if sw * lh <= sh * lw:
        vw, vh = sw, sw * int(lh) // int(lw)
    else:
        vw, vh = sh * int(lw) // int(lh), sh
    viewport = Rect((sw - vw) // 2, (sh - vh) // 2, vw, vh)
    if not viewport.contains(Point(px, py)):
        return "NO_AOI", None
    point = Point((px - viewport.x) * lw / vw, (py - viewport.y) * lh / vh)
    for name, region in (("LEFT_MIRROR", layout.left), ("RIGHT_MIRROR", layout.right),
                         ("REAR_MIRROR", layout.rear), ("FORWARD", layout.forward)):
        if region.contains(point):
            return name, asdict(point)
    return "NO_AOI", asdict(point)
