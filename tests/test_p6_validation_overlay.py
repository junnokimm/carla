import pygame

from src.p6.validation.overlay import Preparation, PreparationOverlay, ValidationKeys


def test_measurement_overlay_draw_is_exactly_no_op() -> None:
    screen = pygame.Surface((1280, 720))
    screen.fill((90, 80, 70))
    before = pygame.image.tobytes(screen, "RGB")
    overlay = PreparationOverlay()

    overlay.draw(screen, None)

    assert pygame.image.tobytes(screen, "RGB") == before
    overlay.draw(screen, Preparation(0, "FORWARD", "FAKE_DISPLAY", True, True))
    assert pygame.image.tobytes(screen, "RGB") != before


def test_space_requires_release_and_suppresses_repeat() -> None:
    keys = ValidationKeys()
    down = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)
    repeat = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, repeat=True)
    up = pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE)

    assert keys.edges([down]) == {pygame.K_SPACE}
    assert keys.edges([down, repeat]) == set()
    assert keys.edges([up, down]) == {pygame.K_SPACE}
    assert keys.edges([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, repeat=True)]) == {pygame.K_ESCAPE}
