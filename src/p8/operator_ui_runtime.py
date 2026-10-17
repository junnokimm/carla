from __future__ import annotations

import pygame

from src.p8.model import Stage
from src.p8.operator_ui import OperatorApp
from src.p8.operator_ui_types import OperatorControl, OperatorUiConfig


def run_operator_ui(controller: OperatorControl, config: OperatorUiConfig) -> int:
    try:
        pygame.init()
        pygame.key.set_repeat(0)
        screen = pygame.display.set_mode(config.window_size)
        pygame.display.set_caption("P8 Unified Operator")
        app = OperatorApp(controller, config)
        app.start()
        while app.running:
            app.handle_events(pygame.event.get())
            if not app.running:
                break
            screen = pygame.display.get_surface() or pygame.display.set_mode(
                config.window_size
            )
            app.render(screen)
            pygame.display.flip()
            app.frame_clock.tick(30)
        if not app.registered:
            return 2
        snapshot = controller.snapshot()
        exported = snapshot.result_export
        return (
            0
            if snapshot.stage is Stage.SESSION_COMPLETE
            and snapshot.closed
            and (exported is None or exported.error is None)
            else 2
        )
    finally:
        pygame.quit()
