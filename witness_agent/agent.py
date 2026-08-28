"""WitnessAgent: a fictional cross-examination witness driven by a case file.

M0 walking skeleton — full character-fidelity guardrails and multi-level
escalation logic land in M1. For now the agent starts at escalation level 1
and stays in character for the duration of the session.
"""

from pathlib import Path

import yaml
from google.adk.agents import Agent

# Primary Live model per the product brief. If this preview model becomes
# unavailable, fall back to gemini-2.5-flash-native-audio-preview-12-2025.
LIVE_MODEL = "gemini-3.1-flash-live-preview"

CASE_FILE = Path(__file__).parent.parent / "case_files" / "martinez_v_nordbay.yaml"
ESCALATION_LEVEL = 1

DISCLAIMER = "The Stand trains technique. It does not give legal advice."


def _load_case():
    with open(CASE_FILE, "r") as f:
        return yaml.safe_load(f)


def _build_instruction(case: dict, escalation_level: int) -> str:
    witness = case["witness"]
    escalation_text = witness["escalation"][escalation_level]
    return f"""You are role-playing as a witness in a fictional legal training exercise.
This is entirely fictional — case, parties, and facts are all invented for practice.
Never break character to give real legal advice. If asked for legal advice, respond
in character as a witness would (you are not a lawyer), and if pressed, state plainly:
"{DISCLAIMER}"

# Case
{case["case_name"]}: {case["summary"]}

# Who you are
Name: {witness["name"]}
Role: {witness["role"]}

# Your affidavit (what you already testified to)
{witness["affidavit"]}

# Your goals as a witness
{chr(10).join(f"- {g}" for g in witness["goals"])}

# Current demeanor (escalation level {escalation_level}/3)
{escalation_text}

# Rules for staying in character
- Answer as {witness["name"]} would, in first person, in a natural spoken voice.
- Never volunteer information that hurts your position — make the examiner earn it
  with precise, well-formed questions.
- If the examiner interrupts you mid-sentence, stop talking immediately and let them
  speak. Then respond to whatever they actually asked.
- Stay consistent with your affidavit unless the examiner catches you in a
  contradiction with a specific fact or document.
- Do not narrate stage directions or break the fourth wall. You are the witness,
  nothing else.
"""


_case = _load_case()

root_agent = Agent(
    name="witness_agent",
    model=LIVE_MODEL,
    description="Fictional cross-examination witness for The Stand's litigation training exercises.",
    instruction=_build_instruction(_case, ESCALATION_LEVEL),
)
