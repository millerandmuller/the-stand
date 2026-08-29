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
