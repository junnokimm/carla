from __future__ import annotations

import math
import time
from dataclasses import astuple, dataclass, replace

import carla
import pygame

from src.p6.tobii.client import TobiiProcessSource
from src.p6.validation.mapping import ValidationError, WindowGeometry
from src.p6.validation.overlay import Preparation, PreparationOverlay, ValidationKeys
from src.p6.validation.run import ValidationRun
from src.p6.validation.windows import WindowProbe
from src.scenario.driver_hud import HUD_SHADOW
from src.scenario.driver_view import DriverView, DriverViewConfig, calculate_layout
from src.scenario.driver_view_presentation import DriverViewPresentation


@dataclass(frozen=True, slots=True)
class LiveOptions:
    host: str
    port: int
    actor_id: int
    max_seconds: int
    fullscreen: bool = True


def validate_geometry(run: ValidationRun, config: DriverViewConfig) -> None:
    layout = calculate_layout(config)
    for candidate, rendered in ((run.layout.rear, layout.rear_mirror),
                                (run.layout.left, layout.left_mirror),
                                (run.layout.right, layout.right_mirror)):
        if astuple(candidate) != rendered.bounds:
            raise ValidationError("Candidate mirrors differ from current production layout")
    if (run.layout.mapping.client.width, run.layout.mapping.client.height) != config.window_size:
        raise ValidationError("Candidate geometry and rendered surface size differ")
    if run.layout.validate().issues:
        raise ValidationError("Candidate geometry has clipping/overlap issues")


def parked(actor: carla.Actor) -> None:
    velocity = actor.get_velocity()
    if math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2) > 0.05:
        raise ValidationError("ACTOR_MOVING: park externally; validation never brakes or controls the vehicle")


def display_geometry(probe: WindowProbe) -> WindowGeometry:
    # set_mode during F11 can replace both the display surface and HWND.
    surface = pygame.display.get_surface()
    hwnd = pygame.display.get_wm_info().get("window")
    if surface is None or not isinstance(hwnd, int):
        raise ValidationError("Pygame display/HWND is unavailable")
    return probe.snapshot(hwnd, surface.get_size())


def execute(run: ValidationRun, source: TobiiProcessSource, options: LiveOptions) -> None:
    base = DriverViewConfig()
    config = replace(base, front_transform=base.cockpit_transform, fov=base.cockpit_fov,
                     rear_mirror_enabled=run.layout.rear_available)
    validate_geometry(run, config)
    client = carla.Client(options.host, options.port)
    client.set_timeout(2.0)
    world = client.get_world()
    if world.get_settings().synchronous_mode:
        raise ValidationError("Use an asynchronously ticking world; validation never ticks or changes world settings")
    actor = world.get_actor(options.actor_id)
    if actor is None or not actor.type_id.startswith("vehicle."):
        raise ValidationError("An existing parked vehicle actor ID is required; validation does not spawn a vehicle")
    parked(actor)
    probe = WindowProbe()
    view: DriverView | None = None
    try:
        pygame.display.init()
        screen = pygame.display.set_mode(config.window_size)
        pygame.display.set_caption("CARLA AOI validation - F11 before C - ESC to abort")
        presentation = DriverViewPresentation(config.window_size, screen)
        if options.fullscreen:
            presentation.handle_events([
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11),
                pygame.event.Event(pygame.KEYUP, key=pygame.K_F11),
            ])
        canvas = presentation.canvas
        run.last_geometry = display_geometry(probe)
        view = DriverView(world, actor, config, read_only=True)
        view.attach()
        source.subscribe(run.sample)
        overlay, keys, clock = PreparationOverlay(), ValidationKeys(), pygame.time.Clock()
        started = time.monotonic_ns()
        while run.sequence.phase not in ("DONE", "ABORTED"):
            now = time.monotonic_ns()
            was_preparing = run.sequence.phase == "PREPARE"
            events = pygame.event.get()
            pressed = keys.edges(events)
            if pygame.K_ESCAPE in pressed or any(event.type == pygame.QUIT for event in events):
                run.sequence.abort(now, "ESC_OR_WINDOW_CLOSE")
                break
            # Keep one fixed coordinate mapping for all measured segments.
            # F11 is available while choosing the monitor, before C confirms it.
            if not run.operator_confirmed:
                presentation.handle_events(events)
            run.sequence.advance(now)
            if run.sequence.phase == "DONE":
                break
            if now - started >= options.max_seconds * 1e9:
                raise ValidationError("VALIDATION_DEADLINE")
            if source.finished.is_set():
                raise ValidationError(f"TOBII_STOPPED:{source.last_error}")
            if run.recorder.error:
                raise ValidationError(run.recorder.error)
            observed = display_geometry(probe)
            hwnd = observed.hwnd
            run.last_geometry = observed
            if run.operator_confirmed and observed != run.geometry:
                raise ValidationError("WINDOW_GEOMETRY_CHANGED_AFTER_CONFIRMATION")
            if run.sequence.phase == "MEASURING" and not probe.focused(hwnd):
                raise ValidationError("WINDOW_FOCUS_LOST")
            parked(actor)
            snapshots = [feed.snapshot() for feed in view.feeds]
            camera_ready = all(item.image is not None for item in snapshots)
            camera_fresh = camera_ready and all(
                item.last_host_receipt_seconds is not None
                and time.perf_counter() - item.last_host_receipt_seconds < 1.0
                for item in snapshots
            )
            gaze_fresh = run.last_sample_ns is not None and now - run.last_sample_ns < 2_000_000_000
            ready = camera_fresh and gaze_fresh and probe.focused(hwnd)
            if run.sequence.windows and not ready:
                raise ValidationError("CAMERA_GAZE_OR_FOCUS_LOST")
            if not ready and now - started > 10_000_000_000:
                raise ValidationError("CAMERA_OR_GAZE_STARTUP_TIMEOUT")
            if pygame.K_c in pressed and ready and not run.operator_confirmed:
                run.confirm(observed, now)
            start_requested = (was_preparing and pygame.K_SPACE in pressed and ready and run.operator_confirmed
                               and run.sequence.phase == "PREPARE")
            canvas.fill(HUD_SHADOW)
            view.draw_preview(canvas)
            preparation = None
            if run.sequence.phase == "PREPARE" and not start_requested:
                monitor = observed.monitor
                label = f"{observed.monitor_name}  ({monitor.x}, {monitor.y}) {monitor.width} x {monitor.height}"
                preparation = Preparation(run.sequence.index, run.sequence.target, label, ready,
                                          run.operator_confirmed, len(run.sequence.targets))
            overlay.draw(canvas, preparation)
            presentation.present()
            pygame.display.flip()
            if start_requested:
                # The software flip-return boundary is logged, not claimed as photon timing.
                run.sequence.start(time.monotonic_ns())
            clock.tick(60)
    except (RuntimeError, ValueError, OSError, pygame.error) as error:
        run.sequence.abort(time.monotonic_ns(), str(error))
        raise
    except KeyboardInterrupt:
        run.sequence.abort(time.monotonic_ns(), "KEYBOARD_INTERRUPT")
        raise
    finally:
        pygame.quit()
        try:
            source.unsubscribe()
        finally:
            try:
                if view is not None:
                    view.close()
            finally:
                probe.close()
