"""Classe base abstrata para todos os repositories do projeto."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from bson import ObjectId
from pymongo.collection import Collection

from database.connection import get_db

logger = logging.getLogger(__name__)


def _to_str_id(doc: dict | None) -> dict | None:
    """
    Converte ObjectId para str em todos os campos de um documento.

    Processa recursivamente sub-documentos. Retorna None se o documento for None.
    """
    if doc is None:
        return None
    result = {}
    for key, val in doc.items():
        if isinstance(val, ObjectId):
            result[key] = str(val)
        elif isinstance(val, dict):
            result[key] = _to_str_id(val)
        elif isinstance(val, list):
            result[key] = [
                _to_str_id(item) if isinstance(item, dict) else item
                for item in val
            ]
        else:
            result[key] = val
    return result


def _serialize_list(docs: list[dict]) -> list[dict]:
    """Converte ObjectId para str em uma lista de documentos."""
    return [_to_str_id(d) for d in docs if d is not None]  # type: ignore[misc]


class BaseRepository(ABC):
    """
    Classe base para todos os repositories do MongoDB.

    Fornece acesso à coleção e métodos CRUD comuns.
    Subclasses devem definir `collection_name`.
    """

    @property
    @abstractmethod
    def collection_name(self) -> str:
        """Nome da coleção MongoDB que este repository gerencia."""
        ...

    @property
    def col(self) -> Collection:
        """Retorna a coleção MongoDB (acesso lazy — conecta sob demanda)."""
        return get_db()[self.collection_name]

    def find_by_id(self, id: str) -> dict | None:
        """
        Busca um documento pelo seu _id.

        Args:
            id: String representando o ObjectId do documento.

        Returns:
            Documento com ObjectId convertido para str, ou None se não encontrado.
        """
        try:
            doc = self.col.find_one({"_id": ObjectId(id)})
            return _to_str_id(doc)
        except Exception as exc:
            logger.error("Erro em find_by_id(%s) em '%s': %s", id, self.collection_name, exc)
            return None

    def delete_by_id(self, id: str) -> bool:
        """
        Remove um documento pelo seu _id.

        Returns:
            True se o documento foi removido, False caso contrário.
        """
        try:
            result = self.col.delete_one({"_id": ObjectId(id)})
            return result.deleted_count > 0
        except Exception as exc:
            logger.error("Erro em delete_by_id(%s) em '%s': %s", id, self.collection_name, exc)
            return False

    def count(self) -> int:
        """Retorna o total de documentos na coleção."""
        try:
            return self.col.count_documents({})
        except Exception as exc:
            logger.error("Erro em count() em '%s': %s", self.collection_name, exc)
            return 0
