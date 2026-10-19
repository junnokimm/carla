from types import SimpleNamespace

from src.scenario.vehicle_audio_assets import GRAIN_SECONDS, NOISE_INDEX, AudioBank


def small_bank(rate, channels):
    # Deterministic silence with real clip length; model/asset tests check audible PCM.
    clip = b"\0\0" * round(rate * GRAIN_SECONDS) * channels
    return AudioBank(rate, channels, (clip,) * (NOISE_INDEX + 2))


class FakeChannel:
    def __init__(self, mixer):
        self.mixer = mixer
        self.until = 0.0
        self.sound = None
        self.volume = 0.0
        self.plays = 0
        self.stops = 0
        self.fades = []

    def get_busy(self):
        return self.mixer.now < self.until

    def play(self, sound, loops=0):
        self.mixer.check("play")
        assert loops == 0  # finite tail is mandatory, no indefinite stale playback
        self.until = self.mixer.now + sound.duration
        self.sound = sound
        self.plays += 1

    def set_volume(self, volume):
        self.mixer.check("volume")
        assert 0 <= volume <= 1
        self.volume = volume

    def stop(self):
        self.stops += 1
        self.mixer.check("stop")
        self.until = 0.0

    def fadeout(self, milliseconds):
        self.fades.append(milliseconds)
        self.until = min(self.until, self.mixer.now + milliseconds / 1000)


class FakeSound:
    def __init__(self, mixer, pcm):
        self.mixer = mixer
        self.duration = len(pcm) / (mixer.config[0] * mixer.config[2] * 2)
        self.selected = None

    def play(self):
        for i in range(self.mixer.reserved, len(self.mixer.channels)):
            if not self.mixer.channels[i].get_busy():
                self.selected = i
                self.mixer.channels[i].play(self)
                return self.mixer.channels[i]
        raise RuntimeError("no automatic channel")

    def stop(self):
        for channel in self.mixer.channels:
            if channel.sound is self:
                channel.stop()


class FakeMixer:
    def __init__(self, config=(22050, -16, 2), fail=None):
        self.config = config
        self.fail = fail
        self.now = 0.0
        self.reserved = 0
        self.channels = [FakeChannel(self) for _ in range(8)]
        self.sound_count = 0
        self.channel_objects = 0
        self.init_count = 0

    def check(self, operation):
        if operation == self.fail:
            raise RuntimeError(f"fake {operation} failure")

    def get_init(self):
        return self.config

    def init(self, **kwargs):
        self.init_count += 1
        self.check("init")
        self.config = (kwargs["frequency"], kwargs["size"], kwargs["channels"])

    def get_num_channels(self):
        return len(self.channels)

    def set_num_channels(self, count):
        assert count >= len(self.channels)  # never truncate someone else's channels
        self.channels.extend(
            FakeChannel(self) for _ in range(count - len(self.channels))
        )

    def set_reserved(self, count):
        self.check("reserve")
        self.reserved = count
        return count

    def Channel(self, index):
        self.channel_objects += 1
        return self.channels[index]

    def Sound(self, *, buffer):
        self.check("sound")
        self.sound_count += 1
        return FakeSound(self, buffer)

    def wait(self, seconds):
        self.now += seconds


def cached_sample(frame=1, stamp=1.0, throttle=0.7, gear=3):
    return SimpleNamespace(
        speed_kmh=60.0,
        control=SimpleNamespace(throttle=throttle, brake=0.0, gear=gear, reverse=False),
        timestamp=SimpleNamespace(
            carla_snapshot=SimpleNamespace(
                frame=frame, host_capture_completed_monotonic_ns=int(stamp * 1e9)
            )
        ),
    )
