# CARLA Driver HMI Design System

## 1. Atmosphere & Identity

The driver HMI is a restrained windshield overlay for a safety-critical research simulator. Its signature is quiet, high-contrast information placed over the camera feed without obscuring mirrors, road context, or existing areas of interest.

## 2. Color

| Role | Token | Value | Usage |
|---|---|---|---|
| Text/primary | `HUD_FOREGROUND` | `(224, 245, 241)` | Primary speed, mode, and navigation text |
| Text/secondary | `HUD_SECONDARY` | `(166, 214, 208)` | Units, gear, key affordances, arrow |
| Depth/shadow | `HUD_SHADOW` | `(5, 15, 18)` | Legibility shadow over the camera feed |
| Tablet/background | `TABLET_BACKGROUND` | `#080b0d` | Participant tablet blank and stimulus field |
| Tablet/stimulus | `TABLET_STIMULUS` | `#dcefed` | All SuRT circles at one contrast level |

Only these existing HUD colors are used. Alpha controls emphasis; no semantic success/error palette or decorative accent is introduced.

## 3. Typography

The existing Windows system UI family is preserved: `segoeui` for Latin telemetry and `malgungothic` for Korean navigation and key labels. Navigation uses the existing mode-toast scale and bold weight; key affordances use the existing unit scale and bold weight. Korean text must render as one natural clause without clipping or orphaned particles.

The participant tablet has no visible instructional, status, error, score, or control text. Browser and operator diagnostics remain in developer tooling rather than the participant surface.

## 4. Spacing & Layout

The base spacing unit is 4 px. Existing speed/gear anchors remain unchanged. Side mirrors occupy the lower-left and lower-right dashboard gaps. When explicitly enabled, the retained rear mirror occupies its existing wide, shallow rectangle below exit navigation. Mirror rectangles are derived from immutable layout configuration so alternate window sizes preserve the same edge and center alignment rules. The forward roadway and central dashboard/HUD remain unobscured.

The tablet stimulus field fills `100dvh` and uses normalized backend coordinates. The development SuRT protocol uses seven circles, 64 px standard diameter, 72 px odd-target diameter, and constant 1.0 contrast. These are protocol tokens, not responsive decoration; target location varies between trials while count, sizes, and contrast remain fixed.

## 5. Components

### Driver HUD
- **Structure**: speed, unit, gear, and optional canonical-mode toast.
- **States**: persistent cockpit telemetry; temporary or canonical mode label.
- **Layout**: existing windshield anchors remain unchanged.

### Exit Navigation Banner
- **Structure**: immutable backend navigation text, right arrow, and optional confirm/reject key labels.
- **Variants**: navigation-only; assisted recommendation with configured keys.
- **States**: absent, recommendation pending presentation, presented, decision/navigation-only.
- **Accessibility**: high-contrast text and shadow; keys are rendered from validated configuration; no color-only meaning.
- **Motion**: none.
- **Layout**: top-center overlay in the non-mirror region.

### Camera Mirror Overlays
- **Structure**: left and right side-view feeds are active by default; the retained center rear-view feed is an explicit opt-in. Every active mirror feed is horizontally flipped to behave as a mirror.
- **Layout**: rear view centered below navigation; side views aligned to the lower window corners using the shared edge-margin token.
- **Surface**: raw camera imagery only, with no panel, border, label, or decorative treatment.
- **Responsive behavior**: positions derive from window dimensions and configured mirror sizes; exported immutable rectangles are the single source for rendering and future AOI mapping.
- **States**: default `rear disabled` omits the rear sensor and image; explicit `rear enabled` restores the existing rear capture, transform, FOV, rectangle, and draw order.

### Tablet Stimulus Field
- **Structure**: one full-viewport stimulus layer; circles are real buttons with no visible button chrome; one full-viewport video element for Module 2.
- **States**: inactive blank, SuRT start-requested/shown, task-paused blank, video loading/playing, experiment-stopped blank, and error blank.
- **Variants**: Module 1 `NO_SURT` is always blank; Module 1 `SURT` shows only the fixed-difficulty circles; Module 2 `MANUAL` and `NOA_L2` use the same supplied media.
- **Accessibility**: circles expose numbered button labels to assistive technology without revealing correctness; no color-only distinction, feedback, reward, or participant control is shown.
- **Motion**: stimulus changes are immediate. A double animation-frame boundary is used only to acknowledge that browser software paint was scheduled; it is not a photon-timing claim.
- **Surface**: `TABLET_BACKGROUND` with `TABLET_STIMULUS`; video uses `object-fit: contain`, muted, non-looping, and playback rate 1.0.

## 6. Motion & Interaction

### Development AOI validation preparation overlay

- Reuse HUD foreground, secondary and shadow colors; Malgun Gothic for Korean instructions.
- Preparation-only type sizes: 28 px target title, 22 px instruction, 18 px monitor/status text; 40 px line spacing and 24 px minimum inset.
- Show target name, Space/C/ESC affordances and the observed monitor identity before capture. No score, pass/fail color or performance feedback.
- Hide every validation instruction before the clean cockpit frame flips; draw no validation overlay during the two-second segment. No countdown animation or gaze cursor.
- Require fresh Space/C edges and key release. C records operator ETM-monitor confirmation, not mapping approval. ESC and focus/geometry changes safely abort.
- This is a development tool only, not participant HMI. Production camera imagery, mirror positions and tablet UI are unchanged.

No animation is added. Confirm/reject actions use fresh `KEYDOWN` edges only, suppress keyboard repeat, and require `KEYUP` before the same physical key can produce another edge.

Tablet interactions add no decorative transitions. Touch acceptance advances to the next SuRT trial without participant feedback. Page visibility changes are recorded as interruptions and never inferred as nonresponse.

## 7. Depth & Surface

The strategy is text shadow only, matching the existing HUD. No panel, border, camera treatment, or mirror treatment is added.

## 8. Accessibility Constraints & Accepted Debt

### Constraints
- Preserve forward-road visibility, exit navigation, and the existing center HUD/dashboard AOI while relocating mirror overlays.
- Keep rear-unavailable AOI space unassigned; do not infer forward, other, or zero-valued gaze for a feed that is not active.
- Render the exact Korean navigation snapshot supplied by the backend.
- Hide confirm/reject affordances when the immutable presentation is not a recommendation.
- Never mark a presentation when the HUD is hidden or the frame did not flip successfully.
- Keep tablet operator controls and protocol errors off the participant display, including `NO_SURT` blank mode.
- Do not describe double-`requestAnimationFrame` acknowledgment as physical display or photon timing.

### Accepted Debt

- Final tablet model, OS, browser, connection, physical display timing, and media file remain unverified until supplied and piloted.
- Rear-disabled operation is a team-approved implementation deviation from the v5 rear-mirror AOI requirement and requires protocol reconciliation before participant use.
