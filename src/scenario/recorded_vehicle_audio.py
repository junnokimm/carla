"""Preloaded finite recordings on three owned mixer channels.

SDL queues contiguous one-second chunks, avoiding frame-timed overlapping
copies of the same waveform. No RPC, file I/O, synthesis, sleeps or worker
threads in update(). If updates stop, at most two seconds remain queued.
"""

from time import monotonic, sleep

from src.scenario.recorded_vehicle_audio_assets import ASSET_ID, recorded_bank
from src.scenario.recorded_vehicle_audio_model import RecordedEngineSoundModel
from src.scenario.vehicle_audio_model import clamp
from src.scenario.vehicle_audio_runtime import VehicleAudioRuntime

VOICE_COUNT = 3
WARNING_DUCK_GAIN = 0.06
WARNING_RELEASE_SECONDS = 0.45


class RecordedVehicleAudioPlayback:
    _owner = None

    def __init__(
        self, mixer, *, bank_provider=recorded_bank, clock=monotonic, wait=sleep
    ):
        self.mixer, self.bank_provider, self.clock, self.wait = (
            mixer,
            bank_provider,
            clock,
            wait,
        )
        self.channels = []
        self.layers = []
        self.format = None
        self.manifest_sha256 = None
        self.available = self.prepared = self.closed = False
        self.error = None
        self.cleanup_errors = []
        self.launches = self.skipped_busy = self.queue_count = 0
        self.underruns = self.duck_requests = 0
        self._next = [0, 0, 0]
        self._started = [False, False, False]
        self._gains = (0.0, 0.0, 0.0)
        self._volumes = [-1, -1, -1]
        self._duck_until = float("-inf")

    def prepare(self):
        if self.prepared or self.closed:
            return
        self.prepared = True
        try:
            if type(self)._owner is not None:
                raise RuntimeError("recorded vehicle audio mixer already owned")
            if self.mixer.get_init() is None:
                self.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            self.format = self.mixer.get_init()
            bank = self.bank_provider(self.mixer, self.format)
            self.manifest_sha256 = bank.manifest_sha256
            count = self.mixer.get_num_channels()
            if any(
                self.mixer.Channel(i).get_busy() for i in range(min(count, VOICE_COUNT))
            ):
                raise RuntimeError("vehicle audio channel range is already in use")
            self.mixer.set_num_channels(max(count, VOICE_COUNT + 8))
            type(self)._owner = self
            if self.mixer.set_reserved(VOICE_COUNT) != VOICE_COUNT:
                raise RuntimeError("recorded vehicle audio reservation failed")
            self.channels = [self.mixer.Channel(i) for i in range(VOICE_COUNT)]
            for layer in bank.layers:
                sounds = []
                for pcm in layer:
                    sound = self.mixer.Sound(buffer=pcm)
                    # Sound gain survives automatic queue advancement and applies
                    # to both current and queued clips, including during warnings.
                    sound.set_volume(0)
                    sounds.append(sound)
                self.layers.append(tuple(sounds))
            if len(self.layers) != VOICE_COUNT or any(
                len(layer) < 2 for layer in self.layers
            ):
                raise ValueError("recorded bank requires three finite loop layers")
            self.available = True
        except Exception as error:  # noqa: BLE001 -- optional audio cannot abort driving
            self._fail(error)

    def _apply_gains(self, duck):
        for index, layer in enumerate(self.layers):
            volume = round(clamp(self._gains[index] * duck) * 128)
            if volume != self._volumes[index]:
                for sound in layer:
                    sound.set_volume(volume / 128)
                self._volumes[index] = volume

    def update(self, parameters, now):
        if not self.available or self.closed:
            return
        try:
            if self.mixer.get_init() != self.format:
                raise RuntimeError("mixer was closed or its format changed")
            self._gains = parameters.gains
            recovery = clamp((now - self._duck_until) / WARNING_RELEASE_SECONDS)
            self._apply_gains(WARNING_DUCK_GAIN + (1 - WARNING_DUCK_GAIN) * recovery)
            for index, channel in enumerate(self.channels):
                if not parameters.fresh:
                    # Do not replenish stale observations. Gain fades down using
                    # the model; finite chunks also expire if Python stops entirely.
                    if self._volumes[index] == 0 and channel.get_busy():
                        channel.stop()
                    self._started[index] = False
                    continue
                layer = self.layers[index]
                if not channel.get_busy():
                    self.underruns += int(self._started[index])
                    channel.play(layer[self._next[index]], loops=0)
                    self._next[index] = (self._next[index] + 1) % len(layer)
                    self._started[index] = True
                    self.launches += 1
                if channel.get_queue() is None:
                    channel.queue(layer[self._next[index]])
                    self._next[index] = (self._next[index] + 1) % len(layer)
                    self.queue_count += 1
        except Exception as error:  # noqa: BLE001 -- preserve input and experiment state
            self._fail(error)

    def duck_warning(self, duration):
        if not self.available or self.closed:
            return {"status": "NO_ACTIVE_VEHICLE_AUDIO"}
        self.duck_requests += 1
        self._duck_until = max(self._duck_until, self.clock() + duration + 0.25)
        self._apply_gains(WARNING_DUCK_GAIN)
        return {
            "status": "APPLIED",
            "gain": WARNING_DUCK_GAIN,
            "hold_s": duration + 0.25,
            "release_s": WARNING_RELEASE_SECONDS,
        }

    def _fail(self, error):
        self.error = f"{type(error).__name__}: {error}"
        self.close(fade=False)

    def _cleanup(self, action):
        try:
            action()
        except Exception as error:  # noqa: BLE001 -- cleanup never replaces primary error
            self.cleanup_errors.append(f"{type(error).__name__}: {error}")

    def close(self, *, fade=True):
        if self.closed:
            return
        self.closed, self.available = True, False
        if type(self)._owner is self:
            if fade and self.channels:
                for channel in self.channels:
                    self._cleanup(lambda channel=channel: channel.fadeout(50))
                self._cleanup(lambda: self.wait(0.05))  # shutdown only
            for channel in self.channels:
                self._cleanup(channel.stop)
            self._cleanup(lambda: self.mixer.set_reserved(0))
            type(self)._owner = None
        self.channels.clear()
        self.layers.clear()


def duck_vehicle_audio_for_warning(mixer, duration):
    """Best effort: a failure here must never suppress the warning itself."""
    owner = RecordedVehicleAudioPlayback._owner
    if owner is None or owner.mixer is not mixer:
        return {"status": "NO_ACTIVE_VEHICLE_AUDIO"}
    try:
        return owner.duck_warning(duration)
    except Exception as error:  # noqa: BLE001 -- disable engine audio, keep warning usable
        owner._fail(error)
        return {"status": "VEHICLE_AUDIO_DISABLED", "error": owner.error}


class RecordedVehicleAudioRuntime(VehicleAudioRuntime):
    def __init__(self, source, playback, *, clock=monotonic):
        super().__init__(source, playback, clock=clock)
        self.model = RecordedEngineSoundModel()

    def status(self):
        report = super().status()
        report.update(
            asset=ASSET_ID,
            rpm_source="NOT_USED_RECORDED_LAYERS",
            manifest_sha256=self.playback.manifest_sha256,
            playback="FINITE_1S_CHUNKS_WITH_SINGLE_QUEUE",
            chunk_starts=self.playback.launches,
            queued_chunks=self.playback.queue_count,
            underruns=self.playback.underruns,
            warning_duck_requests=self.playback.duck_requests,
            session_action="CONTINUE",
        )
        report.pop("grain_launches")
        report.pop("skipped_busy")
        return report
