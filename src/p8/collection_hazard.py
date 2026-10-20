"""Versioned operational responses derived after driving, never a control policy."""

DEFINITION = {
    "version": "P8_RESPONSE_PROXY_V1",
    "window_s": 5.0,
    "brake_threshold": 0.05,
    "steer_delta_threshold": 0.05,
    "hold_s": 0.1,
    "max_gap_s": 0.25,
    "steering_unit": "NORMALIZED_INPUT_NOT_PHYSICAL_DEGREES",
    "onset": "FRAME_MATCHED_DISPLAY_FLIP_PROXY_NOT_PERCEPTION",
}


def hazard_metrics(events, origin_ns, *, precise=False):
    if precise:
        # Same response rules, evaluated on the recorded observation clock only.
        # Never fall back to monotonic for an individual missing input sample.
        converted = []
        missing = 0
        for name, t, payload in events:
            payload = dict(payload)
            if name == "m2_hazard_display_candidate":
                ns = payload.get("flip_completed_perf_counter_ns")
                if ns is None:
                    return {
                        "status": "MISSING_DISPLAY_QPC",
                        "clock_domain": "SHARED_QPC",
                    }
                payload["flip_completed_monotonic_ns"] = ns
            elif name == "m2_hazard_raw_samples":
                samples = []
                for sample in payload.get("samples", []):
                    a, b = (
                        sample.get("poll_started_perf_counter_ns"),
                        sample.get("poll_completed_perf_counter_ns"),
                    )
                    if a is None or b is None or b < a:
                        missing += 1
                        continue
                    samples.append(
                        {
                            **sample,
                            "poll_started_monotonic_ns": a,
                            "poll_completed_monotonic_ns": b,
                        }
                    )
                payload["samples"] = samples
            converted.append((name, t, payload))
        result = hazard_metrics(converted, origin_ns)
        for old, new in (
            ("onset_session_s", "onset_analysis_elapsed_s"),
            ("response_session_s", "response_analysis_elapsed_s"),
            ("response_poll_bounds_ns", "response_poll_bounds_perf_counter_ns"),
            (
                "automatic_brake_readback_session_s",
                "automatic_brake_readback_analysis_elapsed_s",
            ),
        ):
            if old in result:
                result[new] = result.pop(old)
        result.update(clock_domain="SHARED_QPC", missing_qpc_samples=missing)
        if missing:
            result["analysis_reaction_time_s"] = None
            result["time_alignment_eligibility"] = "MISSING_INPUT_QPC"
        return result
    display = next(
        (p for n, t, p in events if n == "m2_hazard_display_candidate"), None
    )
    final = next((p for n, t, p in reversed(events) if n == "m2_hazard_end"), {})
    warning = next((p for n, _, p in events if n == "m2_hazard_warning_displayed"), None)
    collisions = [p for n, _, p in events if n == "m2_hazard_collision_stop_wait"]
    passed = any(n == "m2_hazard_cargo_passed" for n, _, _ in events)
    collision = bool(collisions or final.get("collision_stop"))
    result = {
        "definition": DEFINITION,
        "status": "NO_DISPLAY_ONSET",
        "first_response_type": None,
        "reaction_time_s": None,
        "driver_success": None,
        "human_perception_validated": False,
        "warning": warning,
        "outcome": "COLLISION"
        if collision
        else "PASSED_WITHOUT_RECORDED_COLLISION"
        if passed
        else "NOT_OBSERVED",
    }
    if display is None or display.get("normal_window_candidate") is not True:
        return result
    onset_ns = display["flip_completed_monotonic_ns"]
    t0 = (onset_ns - origin_ns) / 1e9
    samples, batches = [], set()
    for n, _, p in events:
        if n != "m2_hazard_raw_samples" or p.get("batch_id") in batches:
            continue
        batches.add(p.get("batch_id"))
        samples.extend(p.get("samples", []))
    samples.sort(key=lambda s: s["poll_completed_monotonic_ns"])
    before = [s for s in samples if s["poll_completed_monotonic_ns"] < onset_ns]
    baseline = before[-1] if before else None
    valid_baseline = (
        baseline is not None
        and (onset_ns - baseline["poll_completed_monotonic_ns"]) / 1e9 <= 0.25
    )
    base = baseline.get("input", {}) if valid_baseline else {}
    baseline_brake = base.get("brake", 0)
    baseline_steer = base.get("steer", base.get("steering", 0))
    candidates, responses = {}, {}
    last_ns = onset_ns
    gap = not valid_baseline
    last_t = 0
    for s in samples:
        ns = s["poll_completed_monotonic_ns"]
        if ns < onset_ns:
            continue
        t = (ns - onset_ns) / 1e9
        if t > DEFINITION["window_s"] + DEFINITION["max_gap_s"]:
            break
        if (ns - last_ns) / 1e9 > DEFINITION["max_gap_s"]:
            gap = True
            candidates.clear()
        last_ns, last_t = ns, t
        inp = s.get("input", {})
        checks = {
            "BRAKE": baseline_brake < 0.05 and inp.get("brake", 0) >= 0.05,
            "STEER": abs(inp.get("steer", inp.get("steering", 0)) - baseline_steer)
            >= 0.05,
        }
        for name, hit in checks.items():
            if hit:
                if t <= 5:
                    candidates.setdefault(name, (t, s))
                if (
                    name in candidates
                    and t - candidates[name][0] >= 0.1 - 1e-9
                    and name not in responses
                ):
                    responses[name] = candidates[name]
            elif not hit:
                candidates.pop(name, None)
    if responses:
        response_type, (reaction, sample) = min(
            responses.items(), key=lambda p: p[1][0]
        )
        if (
            len(responses) == 2
            and abs(responses["BRAKE"][0] - responses["STEER"][0]) < 1e-9
        ):
            response_type = "BRAKE_AND_STEER"
        geometry = sample.get("geometry", {})
        result.update(
            first_response_type=response_type,
            reaction_time_s=reaction,
            response_session_s=t0 + reaction,
            response_poll_bounds_ns=[
                sample.get("poll_started_monotonic_ns"),
                sample["poll_completed_monotonic_ns"],
            ],
            ttc_candidate_at_response_s=geometry.get("ttc_candidate_s"),
        )
    result.update(
        status="RESPONSE_OBSERVED_WITH_GAPS"
        if responses and gap
        else "RESPONSE_OBSERVED"
        if responses
        else "INSUFFICIENT_DATA"
        if gap or last_t < 5
        else "PREEXISTING_BRAKE_NO_NEW_RESPONSE"
        if baseline_brake >= 0.05
        else "NO_RESPONSE_IN_5S",
        onset_session_s=t0,
        baseline_input=base,
        observation_coverage_s=min(5, last_t),
        outcome="COLLISION"
        if collision
        else "PASSED_WITHOUT_RECORDED_COLLISION"
        if passed
        else "CENSORED",
        onset_geometry=display.get("geometry"),
        source_scene_status=final.get("hazard_state"),
        minimum_ttc_candidate_s=min(
            (
                s["geometry"]["ttc_candidate_s"]
                for s in samples
                if "geometry" in s
                and s["geometry"].get("ttc_candidate_s") is not None
                and s["geometry"]["ttc_candidate_s"] >= 0
                and onset_ns <= s["poll_completed_monotonic_ns"] <= onset_ns + 5e9
            ),
            default=None,
        ),
    )
    automatic_braking = next(
        (
            s
            for s in samples
            if onset_ns <= s["poll_completed_monotonic_ns"] <= onset_ns + 5e9
            and s.get("auto_state") == "NOA_ACTIVE"
            and s.get("applied_control_readback", {}).get("brake", 0) >= 0.05
        ),
        None,
    )
    result["automatic_brake_readback_session_s"] = (
        None
        if automatic_braking is None
        else (automatic_braking["poll_completed_monotonic_ns"] - origin_ns) / 1e9
    )
    result["automatic_brake_semantics"] = (
        "CACHED_CONTROL_READBACK_NOT_RESCUE_OR_PHYSICAL_APPLICATION_PROOF"
    )
    result["safety_rescue_t"] = None
    if gap:
        result["analysis_reaction_time_s"] = None
    else:
        result["analysis_reaction_time_s"] = result["reaction_time_s"]
    if final.get("exposure_policy") == "CLEAR_ROAD_STABLE_TARGET_SPEED_NO_LEAD":
        issues = []
        if warning is None or final.get("warning_errors"):
            issues.append("WARNING_NOT_CONFIRMED_OR_FAILED")
        if final.get("occurrence_result") != "EXPOSURE_CANDIDATE":
            issues.append("SCENE_INVALID_OR_NOT_OCCURRED")
        if automatic_braking is not None:
            issues.append("AUTOMATIC_BRAKE_READBACK_OBSERVED_CAUSE_UNVERIFIED")
        result["experimental_condition_issues"] = issues
        result["experimental_condition_eligible"] = not issues
        if issues:
            result["analysis_reaction_time_s"] = None
    return result
