"use strict";

const root = document.querySelector("#stimulus");
const fragment = new URLSearchParams(window.location.hash.slice(1));
const token = fragment.get("token");
window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);

let currentState = null;
let renderedKey = "";
let pollHandle = null;

const eventId = () => crypto.randomUUID();
const browserClock = () => ({
  performance_ms: performance.now(),
  time_origin_ms: performance.timeOrigin,
});

async function request(path, payload = null) {
  const options = {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  };
  if (payload !== null) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    throw new Error(`${response.status}:${body.error}`);
  }
  return body;
}

function blank() {
  root.replaceChildren();
}

function eventPayload(state) {
  return {
    run_epoch: state.run_epoch,
    trial_uuid: state.current_trial.trial_uuid,
    event_id: eventId(),
    ...browserClock(),
  };
}

async function acknowledgeShown(state) {
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const next = await request("/api/ack", {
    ...eventPayload(state),
    event_type: "shown",
  });
  render(next);
}

function renderSurt(state) {
  const field = document.createDocumentFragment();
  state.circles.forEach((circle, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "surt-circle";
    button.setAttribute("aria-label", `Circle ${index + 1}`);
    button.style.left = `${circle.x * 100}%`;
    button.style.top = `${circle.y * 100}%`;
    button.style.setProperty("--circle-diameter", `${circle.diameter_px}px`);
    button.addEventListener("click", async () => {
      root.querySelectorAll("button").forEach((item) => {
        item.disabled = true;
      });
      try {
        const next = await request("/api/touch", {
          ...eventPayload(state),
          circle_id: circle.circle_id,
        });
        render(next);
      } catch (error) {
        blank();
        console.error("tablet touch failed", error);
      }
    }, { once: true });
    field.append(button);
  });
  root.replaceChildren(field);
  if (state.stage === "START_REQUESTED") {
    void acknowledgeShown(state).catch((error) => {
      blank();
      console.error("tablet paint acknowledgment failed", error);
    });
  }
}

function renderVideo(state) {
  const video = document.createElement("video");
  video.className = "tablet-video";
  video.muted = true;
  video.loop = false;
  video.controls = false;
  video.playsInline = true;
  video.preload = "auto";
  video.src = `/media?token=${encodeURIComponent(token)}`;
  video.addEventListener("loadedmetadata", async () => {
    try {
      const next = await request("/api/video-metadata", {
        ...eventPayload(state),
        duration_seconds: video.duration,
        muted: video.muted,
        loop: video.loop,
        playback_rate: video.playbackRate,
      });
      currentState = next;
      await video.play();
    } catch (error) {
      await reportError(state, `MEDIA_PLAY_FAILED:${String(error)}`);
    }
  }, { once: true });
  video.addEventListener("playing", async () => {
    try {
      currentState = await request("/api/ack", {
        ...eventPayload(state),
        event_type: "media_playing",
      });
    } catch (error) {
      await reportError(state, `MEDIA_PLAYING_ACK_FAILED:${String(error)}`);
    }
  }, { once: true });
  video.addEventListener("ended", async () => {
    try {
      const next = await request("/api/media-ended", {
        ...eventPayload(state),
        current_time_seconds: video.currentTime,
      });
      render(next);
    } catch (error) {
      await reportError(state, `MEDIA_END_ACK_FAILED:${String(error)}`);
    }
  }, { once: true });
  root.replaceChildren(video);
}

async function reportError(state, message) {
  blank();
  console.error(message);
  try {
    await request("/api/error", {
      run_epoch: state.run_epoch,
      trial_uuid: state.current_trial?.trial_uuid ?? null,
      event_id: eventId(),
      message,
    });
  } catch (error) {
    console.error("tablet error report failed", error);
  }
}

function render(state) {
  currentState = state;
  const trial = state.current_trial?.trial_uuid ?? "none";
  const key = `${state.run_epoch}:${trial}:${state.stage}`;
  if (key === renderedKey) {
    return;
  }
  renderedKey = key;
  if (
    state.module === "MODULE_1"
    && state.condition === "SURT"
    && ["START_REQUESTED", "STIMULUS_SHOWN"].includes(state.stage)
    && !state.paused
  ) {
    renderSurt(state);
    return;
  }
  if (state.module === "MODULE_2" && ["START_REQUESTED", "MEDIA_PLAYING"].includes(state.stage)) {
    if (root.querySelector("video") === null) {
      renderVideo(state);
    }
    return;
  }
  blank();
}

async function poll() {
  try {
    render(await request("/api/state"));
  } catch (error) {
    blank();
    console.error("tablet state poll failed", error);
  } finally {
    pollHandle = window.setTimeout(poll, 150);
  }
}

document.addEventListener("visibilitychange", () => {
  if (currentState === null || currentState.current_trial === null) {
    return;
  }
  void request("/api/ack", {
    ...eventPayload(currentState),
    event_type: document.hidden ? "visibility_hidden" : "visibility_visible",
  }).catch((error) => console.error("tablet visibility acknowledgment failed", error));
});

window.addEventListener("pagehide", () => window.clearTimeout(pollHandle), { once: true });

if (token === null || token.length === 0) {
  console.error("tablet token is missing from URL fragment");
  blank();
} else {
  void poll();
  window.setInterval(() => {
    if (currentState !== null) {
      void request("/api/sync-mark", {
        run_epoch: currentState.run_epoch,
        event_id: eventId(),
        ...browserClock(),
      }).catch((error) => console.error("tablet receipt mark failed", error));
    }
  }, 5000);
}
