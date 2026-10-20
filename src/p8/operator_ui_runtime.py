from __future__ import annotations

import pygame

from src.p8.model import Stage
from src.p8.operator_ui import OperatorApp
from src.p8.operator_ui_types import OperatorControl, OperatorUiConfig


def run_operator_ui(controller: OperatorControl, config: OperatorUiConfig) -> int:
    app = None
    try:
        pygame.init()
        pygame.key.set_repeat(0)
        screen = pygame.display.set_mode(config.window_size)
        pygame.display.set_caption("P8 Unified Operator")
        app = OperatorApp(controller, config)
        try:
            app.start()
        except Exception as error:  # noqa: BLE001 -- unexpected errors require visible acknowledgement
            app.hold_runtime_error(error)
        while app.running:
            try:
                app.handle_events(pygame.event.get())
            except Exception as error:  # noqa: BLE001 -- do not close the UI on a runtime error
                app.hold_runtime_error(error)
            if not app.running:
                break
            screen = pygame.display.get_surface() or pygame.display.set_mode(
                config.window_size
            )
            app.render(screen)
            pygame.display.flip()
            app.after_display_flip()
            app.frame_clock.tick(30)
        if not app.registered:
            return 2
        snapshot = controller.snapshot()
        exported = snapshot.result_export
        return (
            0
            if (snapshot.stage is Stage.SESSION_COMPLETE or any(
                run.completion_reason == "HAZARD_COLLISION"
                and run.collision_stop is not None
                and run.collision_stop.get("confirmation") == "SPACE"
                and not run.collision_stop.get("errors")
                for run in snapshot.module_runs
            ))
            and snapshot.closed
            and (exported is None or exported.error is None)
            else 2
        )
    finally:
        if app is not None:
            app.close_warning_practice()
        pygame.quit()
