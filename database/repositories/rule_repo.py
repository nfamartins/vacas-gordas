"""Repository para a coleção 'category_rules' — regras de categorização automática."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bson import ObjectId

from database.repositories.base_repo import BaseRepository, _serialize_list, _to_str_id

logger = logging.getLogger(__name__)


class RuleRepository(BaseRepository):
    """CRUD para regras de categorização usadas pelo rule_engine."""

    @property
    def collection_name(self) -> str:
        return "category_rules"

    def find_active(self) -> list[dict]:
        """
        Retorna regras ativas, ordenadas por priority decrescente.

        Regras com maior prioridade são aplicadas primeiro.
        """
        try:
            docs = list(
                self.col.find({"is_active": True}).sort("priority", -1)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_active de rules: %s", exc)
            return []

    def find_all(self, active_only: bool = False) -> list[dict]:
        """
        Retorna todas as regras (ativas e inativas), ordenadas por priority.

        Args:
            active_only: Se True, retorna apenas as ativas (equivale a find_active).
        """
        try:
            query = {"is_active": True} if active_only else {}
            docs = list(self.col.find(query).sort([("is_active", -1), ("priority", -1)]))
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_all de rules: %s", exc)
            return []

    def insert(self, rule: dict) -> str:
        """
        Insere uma nova regra de categorização.

        Campos esperados: pattern, match_type, category (dict), priority.
        match_type: "contains", "starts_with", "exact", "regex".

        Returns:
            ID da regra inserida como string.
        """
        now = datetime.now(timezone.utc)
        rule.setdefault("is_active", True)
        rule.setdefault("priority", 10)
        rule.setdefault("hit_count", 0)
        rule.setdefault("last_used_at", None)
        rule.setdefault("created_at", now)

        result = self.col.insert_one(rule)
        logger.info(
            "Regra inserida: '%s' → %s",
            rule.get("pattern"),
            rule.get("category", {}).get("full_path"),
        )
        return str(result.inserted_id)

    def increment_hit(self, rule_id: str) -> None:
        """
        Incrementa o contador de uso da regra e registra o timestamp.

        Chamado pelo rule_engine toda vez que uma regra é aplicada com sucesso.
        """
        try:
            self.col.update_one(
                {"_id": ObjectId(rule_id)},
                {"$inc": {"hit_count": 1},
                 "$set": {"last_used_at": datetime.now(timezone.utc)}},
            )
        except Exception as exc:
            logger.error("Erro em increment_hit (%s): %s", rule_id, exc)

    def deactivate(self, rule_id: str) -> bool:
        """
        Desativa uma regra sem removê-la do banco.

        Returns:
            True se a regra foi desativada.
        """
        try:
            result = self.col.update_one(
                {"_id": ObjectId(rule_id)},
                {"$set": {"is_active": False}},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Erro em deactivate de rules (%s): %s", rule_id, exc)
            return False

    def reactivate(self, rule_id: str) -> bool:
        """
        Reativa uma regra previamente desativada.

        Returns:
            True se a regra foi reativada.
        """
        try:
            result = self.col.update_one(
                {"_id": ObjectId(rule_id)},
                {"$set": {"is_active": True}},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Erro em reactivate de rules (%s): %s", rule_id, exc)
            return False

    def update(self, rule_id: str, data: dict) -> bool:
        """
        Atualiza campos de uma regra.

        Args:
            rule_id: ID da regra.
            data:    Campos a atualizar (não inclua _id).

        Returns:
            True se algum documento foi modificado.
        """
        try:
            data.pop("_id", None)
            result = self.col.update_one(
                {"_id": ObjectId(rule_id)},
                {"$set": data},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Erro em update de rules (%s): %s", rule_id, exc)
            return False

    def find_by_pattern(self, pattern: str) -> dict | None:
        """
        Busca uma regra ativa pelo padrão exato.

        Útil para verificar se já existe uma regra antes de criar uma nova.

        Args:
            pattern: Padrão de texto a buscar (deve ser idêntico ao salvo).
        """
        try:
            doc = self.col.find_one({"pattern": pattern, "is_active": True})
            return _to_str_id(doc)
        except Exception as exc:
            logger.error("Erro em find_by_pattern (%s): %s", pattern, exc)
            return None

    def upsert_from_confirmation(
        self,
        description_normalized: str,
        category: dict,
    ) -> str:
        """
        Cria ou atualiza uma regra automaticamente quando o usuário confirma
        uma categoria na página de Categorização.

        Se já existir uma regra para o padrão, atualiza a categoria.
        Se não existir, cria com match_type="contains" e priority=10.

        Args:
            description_normalized: Descrição normalizada (sem acentos, lowercase).
            category: Dicionário de categoria {level1_id, level1_name, ...}.

        Returns:
            ID da regra (nova ou existente) como string.
        """
        existing = self.find_by_pattern(description_normalized)
        now = datetime.now(timezone.utc)

        if existing:
            self.col.update_one(
                {"_id": ObjectId(existing["_id"])},
                {"$set": {"category": category, "last_used_at": now}},
            )
            return existing["_id"]

        rule = {
            "pattern": description_normalized,
            "match_type": "contains",
            "category": category,
            "priority": 10,
        }
        return self.insert(rule)


# Singleton
rule_repo = RuleRepository()
