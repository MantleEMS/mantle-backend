
## Thread Agent

It's a single async function registered as a callback on the Thread router. When any message is written to the `messages` table (via `POST /api/v1/incidents/:id/messages`), the route handler calls the Thread Agent after the database write and Redis publish are complete.

```python
# routers/thread.py

@router.post("/api/v1/incidents/{incident_id}/messages")
async def post_message(incident_id: str, body: MessageCreate, db: AsyncSession):
    # 1. Write message to PostgreSQL
    message = await create_message(db, incident_id, body)
    
    # 2. Publish to Redis for WebSocket broadcast
    await redis.publish(f"thread:{incident_id}", message.to_json())
    
    # 3. Invoke Thread Agent (non-blocking background task)
    if config.ai_mode == "llm" and body.sender_type != "ai":
        background_tasks.add_task(thread_agent.on_message, incident_id, message)
    
    return message
```

The `body.sender_type != "ai"` check prevents infinite loops — the agent doesn't respond to its own messages.

The Thread Agent itself is a Python class with one main method. It's instantiated once when the FastAPI app starts and reused for every invocation. It holds no state between invocations — all context is loaded fresh from the database on every call.

```python
# agent/thread_agent.py

class ThreadAgent:
    def __init__(self, llm_client: LLMClient, tool_registry: ToolRegistry):
        self.llm = llm_client
        self.tools = tool_registry
    
    async def on_message(self, incident_id: str, new_message: Message):
        # Load context fresh every time
        incident = await get_incident(incident_id)
        
        # Skip if incident is resolved
        if incident.status == "resolved":
            return
        
        recent_messages = await get_messages(incident_id, limit=30)
        pending_actions = await get_pending_actions(incident_id)
        sop = await get_sop(incident.sop_id)
        
        # If the new message has an image, analyze it first
        image_context = None
        if new_message.has_attachment("photo"):
            image_context = await self.analyze_image(new_message.attachment_url)
        
        # Build the LLM request
        system_prompt = build_thread_agent_prompt(sop, incident)
        messages = self.format_thread_as_conversation(
            recent_messages, pending_actions, image_context, new_message
        )
        
        # Call the LLM — single agent loop, max 3 tool calls
        result = await self.llm.run_agent(
            system_prompt=system_prompt,
            messages=messages,
            tools=self.tools.get_subset([
                "create_pending_action",
                "post_thread_message",
                "update_incident"
            ]),
            max_iterations=3
        )
        
        # Log the trace for evaluation
        await log_agent_trace(incident_id, "thread_agent", result.trace)
```

That's the entire thing. No framework. No graph. No persistent state. No background process. It's an async function that loads context, calls the LLM once (which may result in 0-3 tool calls internally), and returns.

## The Agent Loop Inside LLMClient

The `run_agent` method on LLMClient is where the tool-calling loop lives. This is the only piece that resembles what LangGraph does — but it's about 40 lines of Python, not a framework.

```python
# agent/llm_client.py

async def run_agent(self, system_prompt, messages, tools, max_iterations=3):
    trace = []
    
    for i in range(max_iterations):
        # Call the LLM
        response = await self._call_provider(system_prompt, messages, tools)
        
        # Extract tool calls and text from the response
        tool_calls = response.get_tool_calls()
        text_output = response.get_text()
        
        # If no tool calls, the agent is done
        if not tool_calls:
            trace.append({"type": "final_text", "content": text_output})
            break
        
        # Execute tool calls (parallel if multiple)
        tool_results = await asyncio.gather(*[
            self.tools.execute(tc.name, tc.params) for tc in tool_calls
        ])
        
        # Log the tool calls and results
        for tc, result in zip(tool_calls, tool_results):
            trace.append({
                "type": "tool_call",
                "tool": tc.name,
                "params": tc.params,
                "result": result
            })
        
        # Append tool calls and results to messages for next iteration
        messages.append(response.raw)  # assistant message with tool_use blocks
        for tc, result in zip(tool_calls, tool_results):
            messages.append({
                "role": "tool",
                "tool_use_id": tc.id,
                "content": json.dumps(result)
            })
    
    return AgentResult(trace=trace)
```

This loop handles both Anthropic's tool_use format and Ollama's OpenAI-compatible function calling format — the `_call_provider` method translates between them. Claude can return multiple tool_use blocks in a single response (parallel tool calls), and `asyncio.gather` executes them concurrently. Ollama models typically return one at a time.

For the Thread Agent with `max_iterations=3`, a typical invocation looks like: iteration 1 — LLM reads the new message and calls `update_incident` (upgrade severity) and `create_pending_action` (recommend armed response), both returned in one response, both executed in parallel. Iteration 2 — LLM calls `post_thread_message` (post an informational message to the thread about what it detected). Iteration 3 — LLM returns text only, no tool calls. Loop ends. Total wall time: 2-4 seconds for Claude, 1-2 seconds for Ollama locally.

Most invocations don't reach iteration 3. If the message was routine ("acknowledged"), the LLM returns no tool calls on the first iteration and the loop exits immediately.

Yes, but differently than the SOP Launcher. The SOP Launcher executes the SOP as a script — step 1, step 2, step 3, in order. The Thread Agent uses the SOP as a rulebook that constrains what it's allowed to recommend and how it interprets the conversation.

## How the SOP Enters the Thread Agent

The SOP definition is loaded from the database and injected into the system prompt on every invocation. The Thread Agent doesn't see a generic instruction like "help during emergencies." It sees the specific SOP that was matched to this incident's emergency type.

```python
def build_thread_agent_prompt(sop: SOP, incident: Incident) -> str:
    return f"""
You are Corvus, the AI safety assistant in an active emergency thread.

## Active SOP: {sop.sop_code} — {sop.name}
Emergency type: {incident.emergency_type}

## SOP Steps (for your reference — the SOP Launcher already executed steps 1-6)
{json.dumps(sop.steps, indent=2)}

## Responder Checklist (track completion against these)
{json.dumps(sop.responder_checklist, indent=2)}

## Your constraints from this SOP:

ESCALATION RULES:
- This is a {incident.emergency_type} incident.
- If a weapon is reported: upgrade severity to 5, recommend confirming 
  armed law enforcement response. This applies ONLY to workplace_violence 
  SOPs. For medical SOPs, a weapon mention means this is no longer purely 
  medical — recommend the commander switch to the workplace violence SOP.
- If additional victims are reported: recommend additional responder dispatch.
- If fire/smoke/gas is mentioned: recommend fire department regardless of 
  original emergency type.
{_build_sop_specific_escalation_rules(sop)}

911 CALL TYPE:
- For SOP-WV-001: 911 calls are for POLICE/THREAT response.
- For SOP-MED-001: 911 calls are for MEDICAL/EMS response.
- You must use the correct call type. Never recommend police for a medical 
  SOP unless a threat is introduced.

RESOLUTION RULES:
- The incident can only be resolved by the commander.
- You may RECOMMEND resolution when: the responder confirms scene is clear, 
  AND the worker confirms they are safe, AND (for violence SOPs) the threat 
  has departed or been neutralized.
- For violence SOPs with a weapon reported: resolution requires confirmation 
  that the weapon is secured. Do not recommend resolution if a weapon was 
  mentioned but not accounted for.
- For medical SOPs: resolution requires EMS handoff OR worker confirmation 
  that the patient is stable.
{_build_sop_specific_resolution_rules(sop)}

RESPONDER GUIDANCE:
- When the responder asks for guidance, reference the responder checklist 
  above. Do not invent steps that are not in the checklist.
- Track which checklist items the responder has completed based on their 
  thread messages. If they skip a step, note it but do not block them.

NOTIFICATION RULES:
{_build_sop_specific_notification_rules(sop)}
- SOP-WV-001: Do NOT notify the patient's emergency contact (the aggressor 
  may be the family member).
- SOP-MED-001: DO recommend notifying the patient's emergency contact if 
  not already done.

OUT OF SCOPE — never do these regardless of SOP:
- Never provide medical advice beyond what the SOP specifies.
- Never instruct the worker to confront an aggressor.
- Never recommend the responder enter an unsecured scene with a known weapon.
- Never override a commander decision.
"""
```

The `_build_sop_specific_*` helper functions read the SOP's `steps` JSONB and extract rules that are specific to that SOP. If a customer configures a custom SOP with different escalation criteria or different resolution conditions, those rules flow into the Thread Agent's prompt automatically.

## What This Means in Practice

Imagine the same conversation — worker says "he has a knife" — under two different SOPs.

**Under SOP-WV-001 (workplace violence):**

The Thread Agent reads the SOP constraints. It knows this is a violence SOP. A weapon mention triggers: upgrade severity to 5, recommend confirming armed law enforcement is en route, advise worker to not engage, advise responder to not enter until police arrive. It calls `update_incident` (severity 5, weapon_involved: true), calls `create_pending_action` ("Confirm armed police response — weapon reported"), and calls `post_thread_message` ("Weapon reported. Advising worker to stay barricaded. Responder should not approach until law enforcement confirms scene is safe.").

**Under SOP-MED-001 (medical):**

The Thread Agent reads the SOP constraints. It knows this is a medical SOP. A weapon mention means this is no longer purely medical. It calls `post_thread_message` ("Worker reports a weapon. This incident may require reclassification from medical to workplace violence."), calls `create_pending_action` ("Recommend switching to SOP-WV-001 — weapon reported during medical response"), and calls `update_incident` (weapon_involved: true). It does NOT automatically upgrade to armed police response because the medical SOP doesn't authorize that — it recommends the commander switch SOPs, and the violence SOP's rules take over once the commander approves.

Same input, different behavior, driven entirely by the SOP in the prompt.

## How the Responder Checklist Is Tracked

The SOP defines a `responder_checklist` — the steps the responder should follow on scene. The Thread Agent tracks completion by matching responder messages against checklist items.

```
SOP-WV-001 responder_checklist:
1. Assess scene safety from outside
2. Identify and de-escalate threat source
3. Ensure worker safety, escort to vehicle
4. Report status to commander

SOP-MED-001 responder_checklist:
1. Assess scene safety
2. Check airway, breathing, circulation
3. Control visible bleeding with direct pressure
4. Report victim status to commander
5. Maintain airway until EMS arrives
```

When the responder posts "I'm outside the house, I can see a man in the driveway," the Thread Agent matches this against checklist item 1 (assess scene safety from outside) and notes it as in progress. When the responder posts "he's walking away, I'm going in," the Thread Agent checks: has scene safety been confirmed? The man left, so arguably yes. But if the SOP is violence and a weapon was reported, the agent flags it: "Note: responder is entering before law enforcement has confirmed scene safety. Weapon was reported at 14:32."

This matching isn't rigid pattern matching — it's the LLM reading the responder's natural language and interpreting it against the checklist steps. "I'm going to check on Maria" matches "ensure worker safety." "His blood pressure is stable" matches "report victim status to commander." The LLM handles the semantic matching that a rule engine can't.

After the incident, the Debrief Agent uses this checklist tracking to produce the SOP compliance section of the report: "Steps 1, 3, 4 completed. Step 2 (de-escalate threat source) was not performed — the threat departed before the responder arrived. This is an acceptable deviation."

## How Custom SOPs Work

The system prompt is generated dynamically from whatever SOP is in the database. If a customer creates a custom SOP — say, SOP-ENV-001 for environmental hazards at a chemical plant — with custom escalation rules ("if worker reports chemical smell, recommend hazmat team"), custom resolution conditions ("resolution requires air quality reading below threshold"), and a custom responder checklist ("don PPE before entering, establish decontamination perimeter"), those rules flow into the Thread Agent's prompt exactly the same way.

The Thread Agent doesn't have hard-coded knowledge of what "workplace violence" or "medical emergency" means. It has hard-coded knowledge of how to read an SOP definition and apply its rules to a conversation. The SOP is the configuration. The agent is the execution engine.

This is why the SOP `steps` JSONB includes structured fields:

```json
{
  "steps": [
    {
      "step": 1,
      "actor": "ai",
      "action": "start_evidence_collection",
      "auto": true,
      "tier": "green",
      "description": "Begin audio recording and GPS streaming"
    },
    {
      "step": 5,
      "actor": "commander",
      "action": "approve_dispatch",
      "auto": false,
      "tier": "red",
      "description": "Dispatch nearest qualified responder",
      "qualification_filter": ["rn", "first_aid"]
    }
  ],
  "escalation_rules": [
    {
      "trigger": "weapon_mentioned",
      "actions": ["upgrade_severity_5", "recommend_armed_response"],
      "applies_to": ["workplace_violence"]
    },
    {
      "trigger": "additional_victim",
      "actions": ["recommend_additional_responder"]
    }
  ],
  "resolution_conditions": [
    "responder_confirms_scene_clear",
    "worker_confirms_safe",
    {"if": "weapon_reported", "then": "weapon_secured_confirmed"}
  ],
  "notification_rules": [
    {
      "notify": "patient_emergency_contact",
      "when": "medical",
      "not_when": "workplace_violence"
    }
  ]
}
```

The SOP Launcher reads `steps` and executes them sequentially. The Thread Agent reads `escalation_rules`, `resolution_conditions`, and `notification_rules` to constrain its live conversation behavior. Same data structure, two different consumers, two different execution models.

## The Evaluation Angle

When you run the quality evaluation, the scoring rubric for "SOP Differentiation" (15% weight) tests exactly this. You run the same scenario — worker reports a weapon — under both SOPs and check that the agent behaves differently. Under violence SOP: escalate to armed response. Under medical SOP: recommend reclassification to violence SOP. If the agent produces the same response regardless of which SOP is loaded, it fails that criterion.

You also test that the agent never violates the SOP's explicit constraints. If the violence SOP says "do not notify patient emergency contact" and the agent recommends notifying them anyway, that's a hard failure on the "Approval Gate Respect" criterion — extended to mean "SOP constraint respect." The agent must stay inside the boundaries the SOP defines, even when the conversation seems to suggest otherwise.

This is the key reason the SOP definition is in the system prompt rather than just referenced as a tool the agent can look up. The system prompt is the strongest constraint mechanism in the Claude API — instructions there carry more weight than instructions in user messages. By putting the SOP constraints in the system prompt, you maximize the probability that the agent honors them even under conversational pressure.