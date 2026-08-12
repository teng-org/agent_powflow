from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi import HTTPException


CHAT_ROOT = Path(__file__).resolve().parents[1]
if str(CHAT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHAT_ROOT))

from api.app import main as chat_api
from runtime.models import ChatSession


def _session(session_id: str = "conversation-1", tenant_id: str = "tenant-a", actor_id: str = "actor-a"):
    return ChatSession(
        session_id=session_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        title="Published agent",
        kb_id="kb-a",
        created_at="2026-08-11T00:00:00+00:00",
        updated_at="2026-08-11T00:00:00+00:00",
    )


class InternalAgentConversationTest(unittest.TestCase):
    def test_published_citations_are_bounded_and_normalized(self) -> None:
        citations = chat_api._published_agent_citations(
            [
                {
                    "doc_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "chunk_index": 3,
                    "document_name": "技术白皮书.pdf",
                    "score": 0.91,
                    "text": "第一行\n   第二行 " + "长" * 300,
                },
                {"doc_id": "doc-2", "source": "案例集.pdf", "score": "not-a-number", "chunk_text": "案例"},
            ],
            limit=1,
        )

        self.assertEqual(1, len(citations))
        self.assertEqual(1, citations[0]["index"])
        self.assertEqual("技术白皮书.pdf", citations[0]["filename"])
        self.assertEqual(0.91, citations[0]["score"])
        self.assertNotIn("\n", citations[0]["text_preview"])
        self.assertLessEqual(len(citations[0]["text_preview"]), 240)

    def test_reuses_owned_conversation(self) -> None:
        existing = _session()
        with patch.object(chat_api.chat_store, "get_session", return_value=existing), patch.object(
            chat_api.chat_store, "create_session"
        ) as create_session:
            resolved = chat_api._resolve_internal_agent_session(
                tenant_id="tenant-a",
                actor_id="actor-a",
                conversation_id="conversation-1",
                title="Agent",
                kb_id="kb-a",
            )
        self.assertIs(existing, resolved)
        create_session.assert_not_called()

    def test_creates_session_when_id_is_blank(self) -> None:
        created = _session()
        with patch.object(chat_api.chat_store, "create_session", return_value=created) as create_session:
            resolved = chat_api._resolve_internal_agent_session(
                tenant_id="tenant-a",
                actor_id="actor-a",
                conversation_id=" ",
                title="Agent",
                kb_id="kb-a",
            )
        self.assertIs(created, resolved)
        create_session.assert_called_once()

    def test_hides_foreign_conversation(self) -> None:
        with patch.object(chat_api.chat_store, "get_session", return_value=_session(tenant_id="tenant-b")):
            with self.assertRaises(HTTPException) as raised:
                chat_api._resolve_internal_agent_session(
                    tenant_id="tenant-a",
                    actor_id="actor-a",
                    conversation_id="conversation-1",
                    title="Agent",
                    kb_id="kb-a",
                )
        self.assertEqual(404, raised.exception.status_code)

    def test_hides_other_users_conversation_in_same_tenant(self) -> None:
        with patch.object(chat_api.chat_store, "get_session", return_value=_session(actor_id="actor-b")):
            with self.assertRaises(HTTPException) as raised:
                chat_api._resolve_internal_agent_session(
                    tenant_id="tenant-a",
                    actor_id="actor-a",
                    conversation_id="conversation-1",
                    title="Agent",
                    kb_id="kb-a",
                )
        self.assertEqual(404, raised.exception.status_code)


if __name__ == "__main__":
    unittest.main()
