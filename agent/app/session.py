"""Persistent per-conversation session (TIP-008b).

Replaces the four in-memory dicts from TIP-006/007/008 (_pii / _action /
_emergency / _hitl) with a single jsonb column conversations.session, so a
service restart no longer drops the PII map (reveal_contact) or in-flight
slots/pending/emergency state.

SessionStore is cache-aside: load() reads the row once (then the in-process
cache), save() writes the row and refreshes the cache — so one turn does exactly
one load + one save regardless of how many graph nodes touch the session.
"""

from dataclasses import dataclass, field
from typing import Any

from app.guardrails.pii import PIISession


@dataclass
class Session:
    pii: PIISession = field(default_factory=PIISession)
    action: dict = field(default_factory=lambda: {"slots": {}, "pending_action": None})
    emergency: dict = field(default_factory=lambda: {"open": False, "asks": 0})
    hitl: dict = field(default_factory=lambda: {"complaint_attempted": False, "handback_note": None})

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        data = data or {}
        return cls(
            pii=PIISession.from_dict(data.get("pii", {})),
            action=data.get("action") or {"slots": {}, "pending_action": None},
            emergency=data.get("emergency") or {"open": False, "asks": 0},
            hitl=data.get("hitl") or {"complaint_attempted": False, "handback_note": None},
        )

    def to_dict(self) -> dict:
        return {
            "pii": self.pii.to_dict(),
            "action": self.action,
            "emergency": self.emergency,
            "hitl": self.hitl,
        }


class SessionStore:
    """Cache-aside reader/writer for conversations.session (service role)."""

    def __init__(self, supabase: Any):
        self.supabase = supabase
        self._cache: dict[str, Session] = {}

    async def load(self, conversation_id: str) -> Session:
        if conversation_id in self._cache:
            return self._cache[conversation_id]
        row = (
            self.supabase.table("conversations")
            .select("session")
            .eq("id", conversation_id)
            .execute()
        )
        data = row.data[0]["session"] if row.data else {}
        session = Session.from_dict(data)
        self._cache[conversation_id] = session
        return session

    async def save(self, conversation_id: str, session: Session) -> None:
        self._cache[conversation_id] = session
        self.supabase.table("conversations").update({"session": session.to_dict()}).eq(
            "id", conversation_id
        ).execute()

    def drop_pii_map(self, session: Session) -> None:
        """Wipe the PII map after a conversation closes — nothing left to unmask."""
        session.pii = PIISession()
