from src.config import CARLA_HOST, CARLA_PORT, CARLA_TIMEOUT


def test_default_config():
    assert CARLA_HOST == "127.0.0.1"
    assert CARLA_PORT == 2000
    assert CARLA_TIMEOUT == 10.0
