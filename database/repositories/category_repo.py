"""Repository para a coleção 'categories' — hierarquia de categorias financeiras."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bson import ObjectId

from database.repositories.base_repo import BaseRepository, _serialize_list, _to_str_id

logger = logging.getLogger(__name__)


class CategoryRepository(BaseRepository):
    """CRUD e consultas hierárquicas para categorias de gastos."""

    @property
    def collection_name(self) -> str:
        return "categories"

    def find_all(self) -> list[dict]:
        """Retorna todas as categorias ativas, ordenadas por full_path."""
        try:
            docs = list(
                self.col.find({"is_active": True}).sort("full_path", 1)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_all de categories: %s", exc)
            return []

    def find_level1(self) -> list[dict]:
        """Retorna apenas categorias raiz (nível 1), ordenadas por nome."""
        try:
            docs = list(
                self.col.find({"level": 1, "is_active": True}).sort("name", 1)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_level1 de categories: %s", exc)
            return []

    def find_children(self, parent_id: str) -> list[dict]:
        """
        Retorna subcategorias (filhos) de uma categoria pai.

        Args:
            parent_id: ID da categoria pai como string.
        """
        try:
            docs = list(
                self.col.find({
                    "parent_id": ObjectId(parent_id),
                    "is_active": True,
                }).sort("name", 1)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_children (%s): %s", parent_id, exc)
            return []

    def find_by_path(self, full_path: str) -> dict | None:
        """
        Busca uma categoria pelo seu full_path desnormalizado.

        Exemplo: "Alimentação > Delivery"
        """
        try:
            doc = self.col.find_one({"full_path": full_path, "is_active": True})
            return _to_str_id(doc)
        except Exception as exc:
            logger.error("Erro em find_by_path (%s): %s", full_path, exc)
            return None

    def insert(self, category: dict) -> str:
        """
        Insere uma nova categoria.

        Args:
            category: Dicionário com name, level, parent_id (opcional),
                      full_path, color, icon, is_active.

        Returns:
            ID da categoria inserida como string.
        """
        now = datetime.now(timezone.utc)
        category.setdefault("is_active", True)
        category.setdefault("color", None)
        category.setdefault("icon", None)
        category.setdefault("created_at", now)

        # Converte parent_id string para ObjectId se necessário
        if "parent_id" in category and isinstance(category["parent_id"], str):
            try:
                category["parent_id"] = ObjectId(category["parent_id"])
            except Exception:
                category["parent_id"] = None

        result = self.col.insert_one(category)
        logger.info(
            "Categoria inserida: %s (nível %d)",
            category.get("full_path"),
            category.get("level", "?"),
        )
        return str(result.inserted_id)

    def build_tree(self) -> list[dict]:
        """
        Retorna a árvore completa de categorias.

        Cada categoria de nível 1 recebe uma chave "children" com
        a lista de suas subcategorias.

        Returns:
            Lista de categorias nível 1, cada uma com "children".
        """
        try:
            level1_list = self.find_level1()
            for cat in level1_list:
                cat["children"] = self.find_children(cat["_id"])
            return level1_list
        except Exception as exc:
            logger.error("Erro em build_tree de categories: %s", exc)
            return []


# Singleton
category_repo = CategoryRepository()
