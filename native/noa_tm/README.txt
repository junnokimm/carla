P8 NoA indicator lane-change client, 2026-10-18

Entry: C:\dev\carla\run_pilot.cmd
This private package is selected before CARLA's first import. The original
.venv-p8 installation and the separate diagnostic client are never overwritten.

Changes: lane_change.patch contains the lane-change and automatic-exit changes.
It is applied to the pinned normal LocalizationStage.cpp. The RPC receive guard
remains linked (see rpc_guard/ and manifest.json). Other native production
objects come from the existing cancellation-patched carla_client.lib. The Python
bindings are rebuilt against that normal client's headers; diagnostic-only
bindings are excluded. Source/library hashes are recorded in manifest.json.

Build: from C:\dev\carla, run the existing .venv-p8 Python with
  -B -m tools.build_noa_tm
Requires VS2022 and the existing C:\dev\carla-native-tm-cancel-20261012 build.
Build outputs and native tests remain in C:\dev\carla\data\noa_tm_build.
After an intentional rebuild, review the native tests and artifact hash before
updating PROJECT_LIBCARLA_SHA256. Link timestamps can change the binary hash.

Offline validation: tests/native/noa_tm_localization.cpp runs the actual native
LocalizationStage using Town04.xodr. It checks left/right target retention at
several speeds/waypoint offsets and a subsequent opposite request. It does not
simulate vehicle physics. Existing request cancellation/registration code is
linked unchanged. No extra vehicle control writer or simulator tick is added.

Lane markings, a valid neighboring Driving lane and verified completion geometry
are still required. A failed/unknown maneuver is never logged as completed.
Live driving remains to be verified by the operator.

2026-10-19 automatic module exit:
The one-shot Right route selects the exit fork only on its final approach road.
LocalizationStage now checks for an empty action list after consuming its last
action. An explicit empty route clears both the owned route and buffered future
waypoints, retaining the current front. This supports cancellation on manual
takeover and participant signals without replay on reactivation. Native tests
cover both Town04 exit forks (39/47), route exhaustion and cancellation in
addition to the original 32 lane-change cases. No hazard warning/braking policy
or vehicle input/deactivation threshold is changed.
