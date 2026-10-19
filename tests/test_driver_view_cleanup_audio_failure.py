from types import SimpleNamespace

import pytest

from src.scenario.driver_view import DriverView
from src.scenario.research_noa_view import ResearchLiveDriverView
from tests.test_driver_view import FakeHero, FakeWorld


@pytest.mark.parametrize("sensor_failure", [False, True])
@pytest.mark.parametrize(
    "audio_error", [RuntimeError("audio close"), KeyboardInterrupt()]
)
def test_audio_close_failure_cannot_skip_camera_cleanup(sensor_failure, audio_error):
    view = DriverView(FakeWorld(), FakeHero())
    trace = []

    def close_audio():
        raise audio_error

    def stop():
        trace.append("stop")
        if sensor_failure:
            raise RuntimeError("sensor stop")

    def destroy():
        trace.append("destroy")

    view._turn_signal_audio = SimpleNamespace(close=close_audio)
    view._feeds = [SimpleNamespace(sensor=SimpleNamespace(stop=stop, destroy=destroy))]
    with pytest.raises(type(audio_error)) as caught:
        view.close()
    assert caught.value is audio_error
    assert trace == ["stop", "destroy"]
    assert not view.feeds
    if sensor_failure:
        assert any("sensor stop" in note for note in caught.value.__notes__)


def test_vehicle_audio_failure_cannot_skip_base_sensor_cleanup():
    view = ResearchLiveDriverView(FakeWorld(), FakeHero())
    trace = []
    error = RuntimeError("vehicle audio close")

    def close_audio(**kwargs):
        raise error

    view._vehicle_audio = SimpleNamespace(close=close_audio)
    view._feeds = [
        SimpleNamespace(
            sensor=SimpleNamespace(
                stop=lambda: trace.append("stop"),
                destroy=lambda: trace.append("destroy"),
            )
        )
    ]
    with pytest.raises(RuntimeError) as caught:
        view.close()
    assert caught.value is error
    assert trace == ["stop", "destroy"]


def test_input_failure_remains_primary_when_audio_cleanup_also_fails(monkeypatch):
    view = ResearchLiveDriverView(FakeWorld(), FakeHero())
    trace = []
    original = RuntimeError("input close")

    def close_input():
        raise original

    def close_audio(**kwargs):
        raise RuntimeError("vehicle audio close")

    monkeypatch.setattr(view, "_close_input", close_input)
    view._tsxw_input_prepared = True
    view._vehicle_audio = SimpleNamespace(close=close_audio)
    view._feeds = [
        SimpleNamespace(
            sensor=SimpleNamespace(
                stop=lambda: trace.append("stop"),
                destroy=lambda: trace.append("destroy"),
            )
        )
    ]
    with pytest.raises(RuntimeError) as caught:
        view.close()
    assert caught.value is original
    assert trace == ["stop", "destroy"]
    assert any("vehicle audio close" in note for note in original.__notes__)


def test_callers_handled_exception_does_not_suppress_cleanup_failure():
    view = ResearchLiveDriverView(FakeWorld(), FakeHero())
    failure = RuntimeError("new audio failure")

    def close_audio(**kwargs):
        raise failure

    view._vehicle_audio = SimpleNamespace(close=close_audio)
    try:
        raise ValueError("already handled by caller")
    except ValueError:
        with pytest.raises(RuntimeError) as caught:
            view.close()
    assert caught.value is failure
