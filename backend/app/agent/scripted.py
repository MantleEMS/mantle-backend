"""
ScriptedAgent — deterministic SOP execution (demo default).
Delegates to the existing sop_executor for backward compatibility.
"""

from uuid import UUID


class ScriptedAgent:
    async def execute(self, incident_id: UUID, org_id: UUID, sop_id: UUID):
        from app.ai.sop_executor import execute_sop_for_incident
        await execute_sop_for_incident(incident_id, org_id, sop_id)
