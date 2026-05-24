"""Repository para a coleção 'accounts' — contas bancárias."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bson import ObjectId

from database.repositories.base_repo import BaseRepository, _serialize_list, _to_str_id

logger = logging.getLogger(__name__)


class AccountRepository(BaseRepository):
    """CRUD completo para contas bancárias."""

    @property
    def collection_name(self) -> str:
        return "accounts"

    def insert(self, account: dict) -> str:
        """
        Insere uma nova conta.

        Args:
            account: Dicionário com os dados da conta. Campos recomendados:
                     name, institution, type, currency, is_active.

        Returns:
            ID da conta inserida como string.
        """
        now = datetime.now(timezone.utc)
        account.setdefault("is_active", True)
        account.setdefault("currency", "BRL")
        account.setdefault("created_at", now)
        account.setdefault("metadata", {"color": None, "icon": None})

        result = self.col.insert_one(account)
        logger.info("Conta inserida: %s (%s)", account.get("name"), result.inserted_id)
        return str(result.inserted_id)

    def find_all(self) -> list[dict]:
        """Retorna todas as contas ordenadas por nome."""
        try:
            docs = list(self.col.find({}).sort("name", 1))
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_all de accounts: %s", exc)
            return []

    def find_by_id(self, id: str) -> dict | None:
        """Busca uma conta pelo ID."""
        try:
            doc = self.col.find_one({"_id": ObjectId(id)})
            return _to_str_id(doc)
        except Exception as exc:
            logger.error("Erro em find_by_id de accounts (%s): %s", id, exc)
            return None

    def find_active(self) -> list[dict]:
        """Retorna apenas contas ativas, ordenadas por nome."""
        try:
            docs = list(self.col.find({"is_active": True}).sort("name", 1))
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_active de accounts: %s", exc)
            return []

    def update(self, id: str, data: dict) -> bool:
        """
        Atualiza campos de uma conta.

        Args:
            id:   ID da conta.
            data: Campos a atualizar (não inclua _id).

        Returns:
            True se algum documento foi modificado.
        """
        try:
            data.pop("_id", None)
            data["updated_at"] = datetime.now(timezone.utc)
            result = self.col.update_one(
                {"_id": ObjectId(id)},
                {"$set": data},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Erro em update de accounts (%s): %s", id, exc)
            return False

    def deactivate(self, id: str) -> bool:
        """
        Desativa uma conta sem removê-la do banco.

        Returns:
            True se a conta foi desativada.
        """
        try:
            result = self.col.update_one(
                {"_id": ObjectId(id)},
                {"$set": {
                    "is_active": False,
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Erro em deactivate de accounts (%s): %s", id, exc)
            return False


# Singleton — importar diretamente nos módulos que precisam
account_repo = AccountRepository()
