"""Firestore session persistence (F8).

ADK 2.8.0's Python package has no native Firestore session service — the
`FirestoreSessionService` in adk-docs (integrations/firestore-session-service)
is Java-only (`Supported in ADK Java`); the Python side only ships
`InMemorySessionService`, `DatabaseSessionService` (SQLAlchemy) and
`VertexAiSessionService` (checked `google.adk.sessions` directly). Per the
brief, the pragmatic fallback is a direct `google-cloud-firestore` client
wrapping the session state we actually care about — case selection,
transcript, scored rubric events, and the debrief — rather than replacing
ADK's own `InMemorySessionService` (which the Runner needs for its internal
event bookkeeping and isn't worth reimplementing for a hackathon timeline).

One document per session in the `the_stand_sessions` collection. Firestore
writes are best-effort: a Firestore outage must never take down a live
voice session, so every write is wrapped and logged, never raised.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

logger = logging.getLogger("the_stand.firestore")

COLLECTION = "the_stand_sessions"
UPLOADED_CASES_COLLECTION = "the_stand_uploaded_cases"


class SessionStore:
    def __init__(self, project: Optional[str] = None):
        try:
            self._client = firestore.AsyncClient(
                project=project or os.environ.get("GOOGLE_CLOUD_PROJECT")
            )
        except Exception as exc:
            logger.warning("Firestore client init failed, persistence disabled: %s", exc)
            self._client = None

    async def start_session(self, session_id: str, case_id: str, pressure_level: int) -> None:
        if not self._client:
            return
        try:
            await self._client.collection(COLLECTION).document(session_id).set(
                {
                    "case_id": case_id,
                    "pressure_level": pressure_level,
                    "started_at": datetime.now(timezone.utc),
                    "transcript_lines": [],
                    "scored_events": [],
                }
            )
        except Exception as exc:
            logger.warning("Firestore start_session failed for %s: %s", session_id, exc)

    async def append_transcript(self, session_id: str, lines: list[str]) -> None:
        if not self._client or not lines:
            return
        try:
            await self._client.collection(COLLECTION).document(session_id).update(
                {"transcript_lines": firestore.ArrayUnion(lines)}
            )
        except Exception as exc:
            logger.warning("Firestore append_transcript failed for %s: %s", session_id, exc)

    async def append_score_events(self, session_id: str, events: list[dict]) -> None:
        if not self._client or not events:
            return
        try:
            await self._client.collection(COLLECTION).document(session_id).update(
                {"scored_events": firestore.ArrayUnion(events)}
            )
        except Exception as exc:
            logger.warning("Firestore append_score_events failed for %s: %s", session_id, exc)

    async def save_debrief(self, session_id: str, debrief: dict) -> None:
        if not self._client:
            return
        try:
            await self._client.collection(COLLECTION).document(session_id).update(
                {"debrief": debrief, "ended_at": datetime.now(timezone.utc)}
            )
        except Exception as exc:
            logger.warning("Firestore save_debrief failed for %s: %s", session_id, exc)


def _to_firestore_case(case: dict) -> dict:
    """PyYAML parses `escalation:\n  1: ...\n  2: ...\n  3: ...` keys as
    Python ints, but Firestore's client can't serialize a map with
    non-string keys (`ValueError: One or more components is not a string or
    is empty` — root-caused during this round's diff re-review: every
    upload's Firestore write was silently failing on exactly this). Round-
    tripped with `_from_firestore_case` below so callers (witness_agent,
    _validate_case) keep seeing int keys, which is what
    `witness["escalation"][escalation_level]` (an int) actually indexes.

    F18's `reverse` block carries its own `escalation` dict with the exact
    same int-keyed shape (see `witness_agent/case_generator.py`
    `build_case_dict`) — found still silently failing every upload whose
    template declares reverse mode (both current upload templates do) during
    this round's privacy-fix verification, because this function only ever
    converted `witness.escalation`, never `reverse.escalation`."""
    out = dict(case)
    witness = out.get("witness")
    if witness and isinstance(witness.get("escalation"), dict):
        out["witness"] = {
            **witness,
            "escalation": {str(k): v for k, v in witness["escalation"].items()},
        }
    reverse = out.get("reverse")
    if reverse and isinstance(reverse.get("escalation"), dict):
        out["reverse"] = {
            **reverse,
            "escalation": {str(k): v for k, v in reverse["escalation"].items()},
        }
    return out


def _from_firestore_case(data: dict) -> dict:
    witness = data.get("witness")
    if witness and isinstance(witness.get("escalation"), dict):
        data = {
            **data,
            "witness": {
                **witness,
                "escalation": {int(k): v for k, v in witness["escalation"].items()},
            },
        }
    reverse = data.get("reverse")
    if reverse and isinstance(reverse.get("escalation"), dict):
        data = {
            **data,
            "reverse": {
                **reverse,
                "escalation": {int(k): v for k, v in reverse["escalation"].items()},
            },
        }
    return data


class UploadedCaseStore:
    """Persists Bring-Your-Own-Case (F16) generated case files — one document
    per uploaded case in `the_stand_uploaded_cases`, keyed by case_id, so a
    generated case "überlebt Neustart" per the prompt's acceptance criterion.
    Same best-effort-write philosophy as SessionStore: never raise into a
    request path, only log. If Firestore is unavailable, uploads simply don't
    persist across restarts (still usable within a single server lifetime via
    the in-process cache in server/app.py).
    """

    def __init__(self, project: Optional[str] = None):
        try:
            self._client = firestore.AsyncClient(
                project=project or os.environ.get("GOOGLE_CLOUD_PROJECT")
            )
        except Exception as exc:
            logger.warning("Firestore client init failed, upload persistence disabled: %s", exc)
            self._client = None

    async def save_case(self, case_id: str, case: dict) -> None:
        if not self._client:
            return
        try:
            await self._client.collection(UPLOADED_CASES_COLLECTION).document(case_id).set(
                {**_to_firestore_case(case), "created_at": datetime.now(timezone.utc)}
            )
        except Exception as exc:
            logger.warning("Firestore save_case failed for %s: %s", case_id, exc)

    async def list_cases(self) -> list[dict]:
        if not self._client:
            return []
        try:
            out = []
            async for doc in self._client.collection(UPLOADED_CASES_COLLECTION).stream():
                data = _from_firestore_case(doc.to_dict())
                data["case_id"] = doc.id
                out.append(data)
            return out
        except Exception as exc:
            logger.warning("Firestore list_cases failed: %s", exc)
            return []

    async def get_case(self, case_id: str) -> Optional[dict]:
        if not self._client:
            return None
        try:
            doc = await self._client.collection(UPLOADED_CASES_COLLECTION).document(case_id).get()
            if not doc.exists:
                return None
            data = _from_firestore_case(doc.to_dict())
            data["case_id"] = doc.id
            return data
        except Exception as exc:
            logger.warning("Firestore get_case failed for %s: %s", case_id, exc)
            return None

    async def delete_case(self, case_id: str) -> None:
        """Manual maintenance operation — not called from any request path.
        For pruning rehearsal/test uploads out of the demo-visible grid. See
        `MAX_UPLOADED_CASES_SHOWN` in server/app.py for the standing cap that
        keeps this from being needed on every future upload."""
        if not self._client:
            return
        try:
            await self._client.collection(UPLOADED_CASES_COLLECTION).document(case_id).delete()
        except Exception as exc:
            logger.warning("Firestore delete_case failed for %s: %s", case_id, exc)
