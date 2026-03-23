"""
Evaluation scenario definitions.
Each scenario describes an emergency type, trigger, and the expected agent behavior.
"""

from dataclasses import dataclass, field


@dataclass
class Scenario:
    id: str
    name: str
    emergency_type: str
    trigger_source: str
    # Tool names expected to be called, in order
    expected_tools: list[str]
    # Number of thread messages expected
    expected_message_count_min: int
    expected_message_count_max: int
    # Tools that MUST appear as create_pending_action (not called directly)
    approval_gate_actions: list[str] = field(default_factory=list)
    # Qualifications filter expected for responder search (for medical)
    expected_qualification_filter: list[str] | None = None
    # 911 call type expected
    expected_911_type: str | None = None


SCENARIOS = [
    Scenario(
        id="S1",
        name="Workplace Violence",
        emergency_type="workplace_violence",
        trigger_source="ui_button",
        expected_tools=[
            "get_sop",
            "get_incident_details",
            "start_evidence_collection",
            "alert_commander",
            "get_worker_profile",
            "get_facility_info",
            "get_available_responders",
            "create_pending_action",  # dispatch
            "create_pending_action",  # 911 police
        ],
        expected_message_count_min=6,
        expected_message_count_max=8,
        approval_gate_actions=["dispatch_responder", "contact_911"],
        expected_911_type="police",
    ),
    Scenario(
        id="S2",
        name="Medical Emergency",
        emergency_type="medical",
        trigger_source="voice",
        expected_tools=[
            "get_sop",
            "get_incident_details",
            "start_evidence_collection",
            "alert_commander",
            "get_worker_profile",
            "get_facility_info",
            "get_patient_info",
            "get_available_responders",
            "create_pending_action",  # 911 medical
            "create_pending_action",  # dispatch
            "create_pending_action",  # notify family
        ],
        expected_message_count_min=7,
        expected_message_count_max=9,
        approval_gate_actions=["dispatch_responder", "contact_911", "notify_emergency_contact"],
        expected_qualification_filter=["rn", "cpr"],
        expected_911_type="medical",
    ),
]

SCENARIO_BY_ID = {s.id: s for s in SCENARIOS}
