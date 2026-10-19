P8 TS-XW existing-resistance preservation (2026-10-18)

Purpose
  Preserve the resistance already provided by the TS-XW driver before launch.
  This does not generate a spring, torque, road effect or rumble effect.
  Actual physical resistance remains subject to the driver/device and requires
  the operator's verification. Offline checks cannot establish physical torque.

Scope
  SDL 2.28.4, x64 Release, official source with one function modified:
    src/joystick/windows/SDL_dinputjoystick.c: SDL_DINPUT_JoystickOpen
  Only USB vendor 044f / product b692 with P8_TSXW_PRESERVE_FORCES=1:
    - DISCL_NONEXCLUSIVE | DISCL_BACKGROUND input acquisition.
    - Skip the force-reset / auto-centering-property initialization block.
  Other devices, or an unset hint, retain the original open behavior.
  Axis format/range/order, buffers, polling, reacquisition and close are unchanged.
  The experiment's steering, throttle, brake, button mappings, hazard evaluation,
  logging, timing and MANUAL/NoA behavior are not edited by this change.

Activation
  run_pilot.cmd now enters tools.run_pilot_preserve_forces, which preloads this
  private SDL before importing the existing tools.run_pilot entry point.
  The arguments, assignment prompt and exit code are forwarded unchanged.
  The private SDL is held for the process lifetime; its environment hint survives
  pygame.quit()/SDL_Quit so training/M1/M2/recovery opens retain the same policy.
  The existing Python environment's installed pygame/SDL files are not replaced.

  Normal commands from C:\dev\carla:
    .\run_pilot.cmd
    .\run_pilot.cmd --m2 1
    .\run_pilot.cmd --m2 2
  Successful preparation prints TSXW_RESISTANCE_PRESERVE_ENABLED. This confirms
  the software policy, not measured physical force. Missing/incompatible assets
  print TSXW_RESISTANCE_UNAVAILABLE and allow the existing pilot to continue.

  Deliberate baseline comparison (bypasses this preservation launcher):
    & 'C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe' -B -u -m tools.run_pilot --m2 1

Manual verification
  Compare wheel resistance immediately before launch and after the pygame window
  opens. Confirm left/right steering, throttle, brake and existing buttons in
  training, then confirm resistance/input through MANUAL/NoA and module changes.
  No real hardware or CARLA driving test was automatically started for this fix.

Rebuild / source / license
  Official source: https://www.libsdl.org/release/SDL2-2.28.4.zip
  SHA256: b53b9b42e731a33552d0a533316a88009b423c16a8a3a418df9ffe498c37da3d
  Source archive/build workspace: data/tsxw_sdl_build (ignored by Git).
  Requires VS 2022 C++ x64 and CMake. With the pilot Python:
    -B tools/build_tsxw_sdl.py --archive data/tsxw_sdl_build/SDL2-2.28.4.zip
  The build script verifies the archive and applies exact source replacements.
  preserve_forces.patch records the complete native source change.
  manifest.json records source, binary and patch hashes and the marked revision.
  SDL copyright/license is preserved in LICENSE.txt. This is a modified local
  build, not an unmodified upstream SDL distribution.

Verification
  -B -m tools.verify_tsxw_sdl_native
    Compiles the actual patched open function against inert DirectInput stubs.
    30 cases: target/other USB IDs, hint on/off, FFB capability, buffered/polled
    devices, acquisition and input-setup errors. No real device calls.
    Verifies the source outside the open function is unchanged.
  -B docs/pilot_stability_verification/run_offline.py --preserve-tsxw-sdl -q
    Preloads this SDL; dummy video/audio and blocked CARLA/joystick opening.
    2205 passed, 7 pre-existing failures, 2 skipped; no new failures.
    Includes 17 new tests for preload/failure handling/argument forwarding and
    actual pygame-to-private-SDL binding, GUID decoding and four AV reinitializations.
  Evidence: docs/pilot_stability_verification/tsxw_native_contract.json,
    tsxw_resistance_tests.xml, tsxw_resistance_tests.txt, tsxw_resistance_summary.json.
