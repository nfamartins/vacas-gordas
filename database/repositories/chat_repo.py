"""Repository para a coleção 'chat_history' — histórico de conversas com a LLM."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from bson import ObjectId

from database.repositories.base_repo import BaseRepository, _serialize_list, _to_str_id

logger = logging.getLogger(__name__)


class ChatRepository(BaseRepository):
    """Persistência do histórico de conversas do chat financeiro."""

    @property
    def collection_name(self) -> str:
        return "chat_history"

    def create_session(self) -> str:
        """
        Cria uma nova sessão de chat vazia.

        Returns:
            session_id como string UUID.
        """
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self.col.insert_one({
            "session_id": session_id,
            "messages": [],
            "created_at": now,
            "updated_at": now,
        })
        logger.debug("Nova sessão de chat criada: %s", session_id)
        return session_id

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        context_used: dict | None = None,
    ) -> None:
        """
        Adiciona uma mensagem ao histórico de uma sessão existente.

        Args:
            session_id:   ID da sessão.
            role:         "user" ou "assistant".
            content:      Texto da mensagem.
            context_used: Metadados do contexto usado pela LLM (opcional).
                          Exemplo: {date_range, categories, transactions_analyzed}.
        """
        now = datetime.now(timezone.utc)
        message: dict = {
            "role": role,
            "content": content,
            "timestamp": now,
        }
        if context_used:
            message["context_used"] = context_used

        try:
            self.col.update_one(
                {"session_id": session_id},
                {
                    "$push": {"messages": message},
                    "$set": {"updated_at": now},
                },
            )
        except Exception as exc:
            logger.error(
                "Erro em append_message (sessão %s): %s", session_id, exc
            )

    def find_session(self, session_id: str) -> dict | None:
        """
        Retorna o documento completo de uma sessão de chat.

        Args:
            session_id: UUID da sessão.
        """
        try:
            doc = self.col.find_one({"session_id": session_id})
            return _to_str_id(doc)
        except Exception as exc:
            logger.error("Erro em find_session (%s): %s", session_id, exc)
            return None

    def find_recent_sessions(self, limit: int = 10) -> list[dict]:
        """
        Retorna as sessões mais recentes com o preview da última mensagem.

        Args:
            limit: Número máximo de sessões a retornar.

        Returns:
            Lista de sessões ordenadas por updated_at decrescente.
        """
        try:
            docs = list(
                self.col.find({}, {
                    "session_id": 1,
                    "created_at": 1,
                    "updated_at": 1,
                    "messages": {"$slice": -1},   # apenas última mensagem (preview)
                })
                .sort("updated_at", -1)
                .limit(limit)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_recent_sessions: %s", exc)
            return []

    def delete_session(self, session_id: str) -> bool:
        """
        Remove permanentemente uma sessão de chat.

        Returns:
            True se a sessão foi removida.
        """
        try:
            result = self.col.delete_one({"session_id": session_id})
            return result.deleted_count > 0
        except Exception as exc:
            logger.error("Erro em delete_session (%s): %s", session_id, exc)
            return False


# Singleton
chat_repo = ChatRepository()
