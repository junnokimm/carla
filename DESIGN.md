# CARLA Driver HMI Design System

## 1. Atmosphere & Identity

The driver HMI is a restrained windshield overlay for a safety-critical research simulator. Its signature is quiet, high-contrast information placed over the camera feed without obscuring mirrors, road context, or existing areas of interest.

## 2. Color

| Role | Token | Value | Usage |
|---|---|---|---|
| Text/primary | `HUD_FOREGROUND` | `(224, 245, 241)` | Primary speed, mode, and navigation text |
| Text/secondary | `HUD_SECONDARY` | `(166, 214, 208)` | Units, gear, key affordances, arrow |
| Depth/shadow | `HUD_SHADOW` | `(5, 15, 18)` | Legibility shadow over the camera feed |

Only these existing HUD colors are used. Alpha controls emphasis; no semantic success/error palette or decorative accent is introduced.

## 3. Typography

The existing Windows system UI family is preserved: `segoeui` for Latin telemetry and `malgungothic` for Korean navigation and key labels. Navigation uses the existing mode-toast scale and bold weight; key affordances use the existing unit scale and bold weight. Korean text must render as one natural clause without clipping or orphaned particles.

## 4. Spacing & Layout

The base spacing unit is 4 px. Existing speed/gear anchors remain unchanged. The rear mirror occupies a wide, shallow top-center rectangle below exit navigation; side mirrors occupy the lower-left and lower-right dashboard gaps. Mirror rectangles are derived from immutable layout configuration so alternate window sizes preserve the same edge and center alignment rules. The forward roadway and central dashboard/HUD remain unobscured.

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
- **Structure**: one center rear-view feed plus left and right side-view feeds, all horizontally flipped to behave as mirrors.
- **Layout**: rear view centered below navigation; side views aligned to the lower window corners using the shared edge-margin token.
- **Surface**: raw camera imagery only, with no panel, border, label, or decorative treatment.
- **Responsive behavior**: positions derive from window dimensions and configured mirror sizes; exported immutable rectangles are the single source for rendering and future AOI mapping.

## 6. Motion & Interaction

No animation is added. Confirm/reject actions use fresh `KEYDOWN` edges only, suppress keyboard repeat, and require `KEYUP` before the same physical key can produce another edge.

## 7. Depth & Surface

The strategy is text shadow only, matching the existing HUD. No panel, border, camera treatment, or mirror treatment is added.

## 8. Accessibility Constraints & Accepted Debt

### Constraints
- Preserve forward-road visibility, exit navigation, and the existing center HUD/dashboard AOI while relocating mirror overlays.
- Render the exact Korean navigation snapshot supplied by the backend.
- Hide confirm/reject affordances when the immutable presentation is not a recommendation.
- Never mark a presentation when the HUD is hidden or the frame did not flip successfully.

### Accepted Debt

None introduced.
