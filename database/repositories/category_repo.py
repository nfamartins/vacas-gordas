"""Repository para a coleção 'categories' — hierarquia de categorias financeiras.

Suporta profundidade arbitrária (N níveis) via parent_id recursivo.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from bson import ObjectId

from database.repositories.base_repo import BaseRepository, _serialize_list, _to_str_id

logger = logging.getLogger(__name__)


class CategoryRepository(BaseRepository):
    """CRUD e consultas hierárquicas para categorias de gastos."""

    @property
    def collection_name(self) -> str:
        return "categories"

    # ── Leitura ───────────────────────────────────────────────────────────────

    def find_all(self, active_only: bool = True) -> list[dict]:
        """
        Retorna categorias ordenadas por full_path.

        Args:
            active_only: Se True (padrão), retorna apenas categorias ativas.
        """
        try:
            query = {"is_active": True} if active_only else {}
            docs = list(self.col.find(query).sort("full_path", 1))
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_all de categories: %s", exc)
            return []

    def find_level1(self, active_only: bool = True) -> list[dict]:
        """Retorna categorias raiz (sem parent_id), ordenadas por nome."""
        try:
            query: dict = {"parent_id": None}
            if active_only:
                query["is_active"] = True
            docs = list(self.col.find(query).sort("name", 1))
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_level1 de categories: %s", exc)
            return []

    def find_children(self, parent_id: str, active_only: bool = True) -> list[dict]:
        """
        Retorna filhos diretos de uma categoria pai.

        Args:
            parent_id:   ID da categoria pai como string.
            active_only: Se True (padrão), retorna apenas filhos ativos.
        """
        try:
            query: dict = {"parent_id": ObjectId(parent_id)}
            if active_only:
                query["is_active"] = True
            docs = list(self.col.find(query).sort("name", 1))
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_children (%s): %s", parent_id, exc)
            return []

    def find_by_path(self, full_path: str) -> dict | None:
        """Busca uma categoria ativa pelo seu full_path desnormalizado."""
        try:
            doc = self.col.find_one({"full_path": full_path, "is_active": True})
            return _to_str_id(doc)
        except Exception as exc:
            logger.error("Erro em find_by_path (%s): %s", full_path, exc)
            return None

    def find_leaf_categories(self, active_only: bool = True) -> list[dict]:
        """
        Retorna apenas categorias folha (sem filhos), ordenadas por full_path.

        Útil para seletores de categorização onde o usuário deve escolher
        a categoria mais específica possível.
        """
        try:
            all_cats = self.find_all(active_only=active_only)
            parent_ids = {c["parent_id"] for c in all_cats if c.get("parent_id")}
            return [c for c in all_cats if c["_id"] not in parent_ids]
        except Exception as exc:
            logger.error("Erro em find_leaf_categories: %s", exc)
            return []

    def build_tree(self, active_only: bool = True) -> list[dict]:
        """
        Retorna a árvore completa de categorias com profundidade arbitrária.

        Usa uma única query e constrói a hierarquia em memória.
        Cada nó recebe uma chave "children" com seus filhos diretos.

        Args:
            active_only: Se True (padrão), inclui apenas categorias ativas.

        Returns:
            Lista de nós raiz, cada um com "children" recursivo.
        """
        try:
            query = {"is_active": True} if active_only else {}
            all_cats = _serialize_list(list(self.col.find(query)))

            # Indexa por _id e inicializa children
            by_id: dict[str, dict] = {}
            for c in all_cats:
                c["children"] = []
                by_id[c["_id"]] = c

            # Distribui filhos e coleta raízes
            roots: list[dict] = []
            for c in all_cats:
                pid = c.get("parent_id")
                if pid and pid in by_id:
                    by_id[pid]["children"].append(c)
                else:
                    roots.append(c)

            # Ordena filhos recursivamente por nome
            def _sort(nodes: list[dict]) -> list[dict]:
                for n in nodes:
                    n["children"] = _sort(sorted(n["children"], key=lambda x: x.get("name", "")))
                return nodes

            return _sort(sorted(roots, key=lambda x: x.get("name", "")))

        except Exception as exc:
            logger.error("Erro em build_tree de categories: %s", exc)
            return []

    # ── Escrita ───────────────────────────────────────────────────────────────

    def insert(self, category: dict) -> str:
        """
        Insere uma nova categoria.

        Campos suportados: name, description, level, parent_id (str/ObjectId/None),
        full_path, color, icon, is_active.

        Returns:
            ID da categoria inserida como string.
        """
        now = datetime.now(timezone.utc)
        category.setdefault("description", "")
        category.setdefault("is_active", True)
        category.setdefault("color", None)
        category.setdefault("icon", None)
        category.setdefault("created_at", now)

        # Converte parent_id string para ObjectId
        if "parent_id" in category and isinstance(category["parent_id"], str):
            try:
                category["parent_id"] = ObjectId(category["parent_id"])
            except Exception:
                category["parent_id"] = None

        result = self.col.insert_one(category)
        logger.info("Categoria inserida: %s", category.get("full_path"))
        return str(result.inserted_id)

    def update(self, id: str, data: dict) -> dict:
        """
        Atualiza nome, descrição e/ou categoria mãe.

        Se o nome ou a categoria mãe mudar, atualiza full_path e level para
        esta categoria e em cascata para todos os seus descendentes.

        Args:
            id:   ID da categoria a atualizar.
            data: Campos a alterar. Chaves suportadas:
                  - name (str)
                  - description (str)
                  - parent_id (str | None)  — None = tornar raiz

        Returns:
            {"updated": bool, "categories_cascaded": int}
        """
        old_cat = self.find_by_id(id)
        if not old_cat:
            return {"updated": False, "categories_cascaded": 0}

        update_fields: dict = {}
        old_full_path: str = old_cat["full_path"]
        new_full_path: str = old_full_path

        if "description" in data:
            update_fields["description"] = data["description"]

        new_name = data.get("name", old_cat["name"]).strip()
        parent_changed = "parent_id" in data
        name_changed = new_name != old_cat["name"]

        if name_changed or parent_changed:
            update_fields["name"] = new_name

            # Calcula novo parent_path
            if parent_changed:
                new_pid = data["parent_id"]
                if new_pid:
                    parent_doc = self.find_by_id(str(new_pid))
                    parent_path = parent_doc["full_path"] if parent_doc else ""
                else:
                    parent_path = ""  # tornar raiz
                new_parent_oid = ObjectId(new_pid) if new_pid else None
                update_fields["parent_id"] = new_parent_oid
            else:
                # Mesmo pai: extrai parent_path do full_path atual
                parts = old_full_path.split(" > ")
                parent_path = " > ".join(parts[:-1])

            new_full_path = f"{parent_path} > {new_name}" if parent_path else new_name
            update_fields["full_path"] = new_full_path
            update_fields["level"] = len(new_full_path.split(" > "))

        # Aplica atualização neste documento
        result = self.col.update_one({"_id": ObjectId(id)}, {"$set": update_fields})

        # Cascata full_path em todos os descendentes
        cats_cascaded = 0
        if old_full_path != new_full_path:
            cats_cascaded = self._cascade_full_path_update(old_full_path, new_full_path)

        return {
            "updated": result.modified_count > 0 or bool(update_fields),
            "categories_cascaded": cats_cascaded,
        }

    def deactivate(self, id: str) -> bool:
        """Desativa uma categoria (preserva histórico das transações)."""
        try:
            result = self.col.update_one(
                {"_id": ObjectId(id)},
                {"$set": {"is_active": False}},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Erro em deactivate de categories (%s): %s", id, exc)
            return False

    def reactivate(self, id: str) -> bool:
        """Reativa uma categoria previamente desativada."""
        try:
            result = self.col.update_one(
                {"_id": ObjectId(id)},
                {"$set": {"is_active": True}},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Erro em reactivate de categories (%s): %s", id, exc)
            return False

    # ── Helpers internos ──────────────────────────────────────────────────────

    def _cascade_full_path_update(self, old_prefix: str, new_prefix: str) -> int:
        """
        Atualiza full_path e level de todos os descendentes cujo caminho
        começa com `old_prefix > `.

        Returns:
            Número de categorias descendentes atualizadas.
        """
        pattern = f"^{re.escape(old_prefix)} > "
        descendants = list(self.col.find({"full_path": {"$regex": pattern}}))
        count = 0
        for desc in descendants:
            old_path = desc["full_path"]
            # Substitui apenas o prefixo (não substitui ocorrências internas)
            new_path = new_prefix + old_path[len(old_prefix):]
            new_level = len(new_path.split(" > "))
            self.col.update_one(
                {"_id": desc["_id"]},
                {"$set": {"full_path": new_path, "level": new_level}},
            )
            count += 1
        return count


# Singleton
category_repo = CategoryRepository()
