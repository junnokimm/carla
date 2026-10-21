"""Mixer with finite automatic queue advancement and persistent Sound gains."""

from src.scenario.recorded_vehicle_audio_assets import RecordedAudioBank
from tests.vehicle_audio_fakes import FakeChannel, FakeMixer, FakeSound


def recorded_test_bank(mixer, config):
    rate, _, channels = config
    return RecordedAudioBank(
        ((b"\0\0" * rate * channels,) * 3,) * 3, "offline-test-bank"
    )


class QueuedChannel(FakeChannel):
    def __init__(self, mixer):
        super().__init__(mixer)
        self.pending = None
        self.history = []

    def _advance(self):
        if self.mixer.now >= self.until and self.pending is not None:
            self.sound, self.pending = self.pending, None
            self.until += self.sound.duration
            self.history.append(self.sound)

    def get_busy(self):
        self._advance()
        return super().get_busy()

    def get_queue(self):
        self.mixer.check("get_queue")
        self._advance()
        return self.pending

    def queue(self, sound):
        self.mixer.check("queue")
        if self.get_busy():
            self.pending = sound
        else:
            self.play(sound)

    def play(self, sound, loops=0):
        super().play(sound, loops)
        self.pending = None
        self.history.append(sound)

    def stop(self):
        super().stop()
        self.pending = None


class VolumeSound(FakeSound):
    def __init__(self, mixer, pcm):
        super().__init__(mixer, pcm)
        self.volume = 1.0

    def set_volume(self, value):
        self.mixer.check("volume")
        assert 0 <= value <= 1
        self.volume = value


class RecordedFakeMixer(FakeMixer):
    def __init__(self, config=(44100, -16, 2), fail=None):
        super().__init__(config, fail)
        self.channels = [QueuedChannel(self) for _ in range(8)]

    def set_num_channels(self, count):
        assert count >= len(self.channels)
        self.channels.extend(
            QueuedChannel(self) for _ in range(count - len(self.channels))
        )

    def Sound(self, *, buffer):
        self.check("sound")
        self.sound_count += 1
        return VolumeSound(self, buffer)
