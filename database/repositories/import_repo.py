"""Repository para a coleção 'imports' — histórico de importações de arquivos."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bson import ObjectId

from database.repositories.base_repo import BaseRepository, _serialize_list, _to_str_id

logger = logging.getLogger(__name__)


class ImportRepository(BaseRepository):
    """Registro e controle de importações de arquivos financeiros."""

    @property
    def collection_name(self) -> str:
        return "imports"

    def insert(self, import_doc: dict) -> str:
        """
        Registra uma nova importação.

        Campos esperados: account_id, filename, file_hash, file_type,
        source_type, parser_used. Status inicial: "processing".

        Returns:
            ID do registro inserido como string.
        """
        import_doc.setdefault("status", "processing")
        import_doc.setdefault("stats", {})
        import_doc.setdefault("errors", [])
        import_doc.setdefault("imported_at", datetime.now(timezone.utc))

        # Converte account_id string para ObjectId
        if "account_id" in import_doc and isinstance(import_doc["account_id"], str):
            try:
                import_doc["account_id"] = ObjectId(import_doc["account_id"])
            except Exception:
                pass

        result = self.col.insert_one(import_doc)
        logger.info(
            "Importação registrada: '%s' (conta: %s)",
            import_doc.get("filename"),
            import_doc.get("account_id"),
        )
        return str(result.inserted_id)

    def find_by_hash(self, file_hash: str) -> dict | None:
        """
        Verifica se um arquivo já foi importado pelo seu hash SHA-256.

        Usado para prevenir reimportação acidental do mesmo arquivo.

        Args:
            file_hash: Hash no formato "sha256:<hex>".

        Returns:
            Registro da importação anterior, ou None se não encontrado.
        """
        try:
            doc = self.col.find_one({"file_hash": file_hash, "status": "completed"})
            return _to_str_id(doc)
        except Exception as exc:
            logger.error("Erro em find_by_hash (%s): %s", file_hash, exc)
            return None

    def update_status(
        self,
        import_id: str,
        status: str,
        stats: dict,
        errors: list | None = None,
    ) -> None:
        """
        Atualiza o status e as estatísticas de uma importação após processamento.

        Args:
            import_id: ID do registro de importação.
            status:    "completed", "failed" ou "partial".
            stats:     Dicionário com total_transactions, inserted,
                       duplicates_skipped, pending_categorization.
            errors:    Lista de mensagens de erro (opcional).
        """
        try:
            update: dict = {
                "status": status,
                "stats": stats,
                "imported_at": datetime.now(timezone.utc),
            }
            if errors is not None:
                update["errors"] = errors

            self.col.update_one(
                {"_id": ObjectId(import_id)},
                {"$set": update},
            )
        except Exception as exc:
            logger.error("Erro em update_status (%s): %s", import_id, exc)

    def find_recent(self, limit: int = 20) -> list[dict]:
        """
        Retorna as importações mais recentes, ordenadas por data decrescente.

        Args:
            limit: Número máximo de registros a retornar.
        """
        try:
            docs = list(
                self.col.find({})
                .sort("imported_at", -1)
                .limit(limit)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_recent de imports: %s", exc)
            return []

    def find_by_account(self, account_id: str) -> list[dict]:
        """
        Retorna importações de uma conta específica, ordenadas por data.

        Args:
            account_id: ID da conta como string.
        """
        try:
            docs = list(
                self.col.find({"account_id": ObjectId(account_id)})
                .sort("imported_at", -1)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_by_account de imports (%s): %s", account_id, exc)
            return []


# Singleton
import_repo = ImportRepository()
