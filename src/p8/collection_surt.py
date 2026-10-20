"""Reference for separately collected Android SuRT logs; no app control/import."""


def external_surt_reference(manifest, windows):
    condition = manifest["study_context"]["module_1_condition"]
    return {
        "schema_version": "EXTERNAL_SURT_REFERENCE_V1",
        "status": "EXTERNAL_APP_COLLECTS_SEPARATELY"
        if condition == "SURT"
        else "NOT_APPLICABLE_NO_SURT",
        "run_id": manifest["run_id"],
        "participant_id": manifest["participant_id"],
        "m1_condition": condition,
        "m1_surt_assigned": condition == "SURT",
        "condition_basis": "REGISTERED_M1_ASSIGNMENT_NOT_APK_DETECTION",
        "actual_app_execution_verified": False,
        "m1_active_windows_session_s": windows,
        "pc_session_origin": manifest["origin"],
        "external_app": "SuRT Android APK",
        "app_logs_inspected": False,
        "tablet_clock_alignment": "PENDING_EXTERNAL_LOG_CLOCK_EVIDENCE",
        "stimulus_response_metrics_source": "EXTERNAL_APP_ORIGINAL_LOGS",
        "join_keys": ["participant_id", "run_id", "m1_condition"],
        "operator_note": "Keep this reference with the same participant's exported SuRT log. "
        "Match clock domains using real paired markers before comparing app events to vehicle/gaze time. "
        "PC UTC/origin alone does not prove tablet synchronization.",
        "trial_count": None,
        "correct_count": None,
        "timeout_count": None,
        "missing_value_rule": "EXTERNAL_LOG_NOT_READ_IS_NULL_NOT_ZERO",
    }
