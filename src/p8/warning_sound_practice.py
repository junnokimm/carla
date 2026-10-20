"""Acknowledge the same warning cue before the timed training drive starts."""

from time import monotonic

from src.p8.operator_render import OperatorPage
from src.scenario.hazard_warning_audio import DURATION_SECONDS, HazardWarningAudio


class WarningSoundPractice:
    def __init__(self, controller, *, audio=None, clock=None):
        self.controller = controller
        self.audio = HazardWarningAudio() if audio is None else audio
        self.clock = monotonic if clock is None else clock
        self.presented = False
        self.drawn = False
        self.ready_at = 0.0
        self.last_play = None
        self.closed = False
        self.recording_errors = []
        self.audio.prepare()

    def _emit(self, name, payload):
        try:
            self.controller.session.services.emit(
                "training_warning_audio_" + name,
                {**payload, "session_action": "CONTINUE",
                 "recording_errors": list(self.recording_errors)},
            )
        except Exception as error:  # noqa: BLE001 -- optional familiarization logging
            detail = f"{type(error).__name__}:{error}"
            if detail not in self.recording_errors and len(self.recording_errors) < 16:
                self.recording_errors.append(detail)

    def after_display_flip(self):
        if self.closed or self.presented or not self.drawn:
            return
        self.presented = True
        self.play("AUTOMATIC_FIRST_PRESENTATION")

    def play(self, source):
        if self.closed or not self.presented or self.clock() < self.ready_at:
            return
        self.last_play = self.audio.play()
        self.ready_at = self.clock() + (
            DURATION_SECONDS if self.last_play["play_request_accepted"] else 0.0
        )
        self._emit("play_requested", {
            "source": source, "audio": self.last_play,
            "audio_preparation": self.audio.summary(),
        })

    def acknowledge(self):
        if self.closed or not self.presented or self.clock() < self.ready_at:
            return False
        self._emit("acknowledged", {
            "confirmation": "SPACE", "meaning_instructions_acknowledged": True,
            "physical_audibility_verified": False, "last_play": self.last_play,
        })
        self.close("ACKNOWLEDGED")
        return True

    def close(self, reason):
        if self.closed:
            return
        self.closed = True
        self.audio.close()
        self._emit("closed", {"reason": reason, "audio": self.audio.summary()})

    def page(self):
        unavailable = self.last_play is not None and not self.last_play["play_request_accepted"]
        return OperatorPage(
            "훈련 · 경고음 안내", "경고음 익히기",
            (
                "지금 들리는 소리는 전방 위험을 알리는 경고음입니다.",
                "실제 주행에서 이 소리가 들리면 전방을 확인하고 필요한 회피 조작을 해 주세요.",
                "NoA 사용 중 인수 요청이 함께 표시되면 직접 운전해 주세요.",
                "지금은 경고음 연습이며 위험사건은 발생하지 않습니다.",
                "소리가 들리지 않으면 진행자에게 알려 주세요.",
                "소리 재생에 실패했습니다. 화면 안내를 확인한 뒤 계속 진행할 수 있습니다."
                if unavailable else "경고음의 의미를 확인한 뒤 훈련 주행을 시작합니다.",
            ),
            ("R  경고음 다시 듣기", "SPACE  안내 확인 후 훈련 시작", "ESC  실험 중단"),
            "음향 사용 불가 · 화면 경고는 유지됩니다" if unavailable else "경고음 안내 · 훈련 주행 시작 전",
        )
