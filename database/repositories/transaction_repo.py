"""Repository para a coleção 'transactions' — o repository mais importante do projeto.

Gerencia todas as operações de leitura, escrita e agregação de transações financeiras.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bson import ObjectId
from pymongo.errors import BulkWriteError

from database.repositories.base_repo import BaseRepository, _serialize_list, _to_str_id

logger = logging.getLogger(__name__)


class TransactionRepository(BaseRepository):
    """Operações CRUD e agregações para transações financeiras."""

    @property
    def collection_name(self) -> str:
        return "transactions"

    # ── Escrita ───────────────────────────────────────────────────────────────

    def insert_many(self, transactions: list[dict]) -> dict:
        """
        Insere múltiplas transações ignorando duplicatas pelo índice único em dedup_key.

        Usa ordered=False para que erros de chave duplicada não interrompam o lote.
        Requer que o índice único em 'dedup_key' exista (criado por scripts/init_db.py).

        Args:
            transactions: Lista de documentos MongoDB prontos para inserção.

        Returns:
            Dicionário com:
            - inserted (int): número de documentos inseridos com sucesso.
            - skipped  (int): número de duplicatas ignoradas.
        """
        if not transactions:
            return {"inserted": 0, "skipped": 0}

        try:
            result = self.col.insert_many(transactions, ordered=False)
            return {"inserted": len(result.inserted_ids), "skipped": 0}
        except BulkWriteError as exc:
            details = exc.details
            inserted = details.get("nInserted", 0)
            skipped = len(transactions) - inserted
            logger.info(
                "insert_many: %d inserida(s), %d duplicata(s) ignorada(s).",
                inserted, skipped,
            )
            return {"inserted": inserted, "skipped": skipped}
        except Exception as exc:
            logger.error("Erro em insert_many de transactions: %s", exc, exc_info=True)
            return {"inserted": 0, "skipped": len(transactions)}

    def update_category(
        self,
        transaction_id: str,
        category: dict,
        method: str,
    ) -> bool:
        """
        Atualiza a categoria de uma transação e marca como confirmada.

        Args:
            transaction_id: ID da transação.
            category: Dicionário com {level1_id, level1_name, level2_id, level2_name, full_path}.
            method:  Método de categorização: "llm", "rule" ou "manual".

        Returns:
            True se o documento foi atualizado.
        """
        now = datetime.now(timezone.utc)
        try:
            result = self.col.update_one(
                {"_id": ObjectId(transaction_id)},
                {"$set": {
                    "category": category,
                    "categorization.status": "confirmed",
                    "categorization.method": method,
                    "categorization.confirmed_by_user": True,
                    "categorization.confirmed_at": now,
                    "updated_at": now,
                }},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error(
                "Erro em update_category (%s): %s", transaction_id, exc, exc_info=True
            )
            return False

    def update_ignored(self, transaction_id: str, ignored: bool) -> bool:
        """
        Marca ou desmarca uma transação como ignorada.

        Returns:
            True se o documento foi atualizado.
        """
        try:
            result = self.col.update_one(
                {"_id": ObjectId(transaction_id)},
                {"$set": {
                    "is_ignored": ignored,
                    "categorization.status": "ignored" if ignored else "pending",
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            return result.modified_count > 0
        except Exception as exc:
            logger.error("Erro em update_ignored (%s): %s", transaction_id, exc)
            return False

    # ── Leitura ───────────────────────────────────────────────────────────────

    def find_pending(self) -> list[dict]:
        """
        Retorna transações com status 'pending', ordenadas por data decrescente.

        Exclui transações já ignoradas.
        """
        try:
            docs = list(
                self.col.find({
                    "categorization.status": "pending",
                    "is_ignored": False,
                }).sort("date", -1)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_pending: %s", exc)
            return []

    def find_by_period(self, start: datetime, end: datetime) -> list[dict]:
        """
        Retorna transações dentro de um período, ordenadas por data decrescente.

        Args:
            start: Data/hora de início (inclusive).
            end:   Data/hora de fim (inclusive).
        """
        try:
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")
            docs = list(
                self.col.find({
                    "date": {"$gte": start_str, "$lte": end_str},
                }).sort("date", -1)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_by_period: %s", exc)
            return []

    def find_by_category(self, full_path: str) -> list[dict]:
        """Retorna transações de uma categoria específica pelo full_path."""
        try:
            docs = list(
                self.col.find({"category.full_path": full_path}).sort("date", -1)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_by_category (%s): %s", full_path, exc)
            return []

    def find_by_account(self, account_id: str) -> list[dict]:
        """Retorna transações de uma conta específica, ordenadas por data."""
        try:
            docs = list(
                self.col.find({"account_id": ObjectId(account_id)}).sort("date", -1)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_by_account (%s): %s", account_id, exc)
            return []

    def find_all(self, limit: int = 100, skip: int = 0) -> list[dict]:
        """
        Retorna transações com paginação, ordenadas por data decrescente.

        Args:
            limit: Máximo de documentos a retornar.
            skip:  Número de documentos a pular (para paginação).
        """
        try:
            docs = list(
                self.col.find({})
                .sort("date", -1)
                .skip(skip)
                .limit(limit)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_all de transactions: %s", exc)
            return []

    def find_filtered(
        self,
        start: str | None = None,
        end: str | None = None,
        account_id: str | None = None,
        category_path: str | None = None,
        status: str | None = None,
        tx_type: str | None = None,
        limit: int = 500,
        skip: int = 0,
    ) -> list[dict]:
        """
        Busca transações com múltiplos filtros opcionais.

        Args:
            start:        Data início no formato YYYY-MM-DD.
            end:          Data fim no formato YYYY-MM-DD.
            account_id:   ID da conta (string).
            category_path: full_path da categoria.
            status:       "pending", "confirmed", "ignored" ou "auto".
            tx_type:      "debit" ou "credit".
            limit:        Máximo de resultados.
            skip:         Deslocamento para paginação.
        """
        query: dict = {}

        if start or end:
            date_filter: dict = {}
            if start:
                date_filter["$gte"] = start
            if end:
                date_filter["$lte"] = end
            query["date"] = date_filter

        if account_id:
            try:
                query["account_id"] = ObjectId(account_id)
            except Exception:
                pass

        if category_path:
            query["category.full_path"] = category_path

        if status:
            query["categorization.status"] = status

        if tx_type:
            query["type"] = tx_type

        try:
            docs = list(
                self.col.find(query)
                .sort("date", -1)
                .skip(skip)
                .limit(limit)
            )
            return _serialize_list(docs)
        except Exception as exc:
            logger.error("Erro em find_filtered: %s", exc)
            return []

    # ── Contagens ─────────────────────────────────────────────────────────────

    def count_pending(self) -> int:
        """Retorna o número de transações aguardando categorização."""
        try:
            return self.col.count_documents({
                "categorization.status": "pending",
                "is_ignored": False,
            })
        except Exception as exc:
            logger.error("Erro em count_pending: %s", exc)
            return 0

    # ── Agregações ────────────────────────────────────────────────────────────

    def aggregate_by_category(
        self,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """
        Soma gastos agrupados por category.full_path no período.

        Considera apenas débitos não ignorados com categoria definida.

        Returns:
            Lista de dicts com {_id, level1, level2, total, count},
            ordenada por total decrescente.
        """
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        pipeline = [
            {"$match": {
                "date": {"$gte": start_str, "$lte": end_str},
                "type": "debit",
                "is_ignored": False,
                "category.full_path": {"$ne": None},
            }},
            {"$group": {
                "_id": "$category.full_path",
                "level1": {"$first": "$category.level1_name"},
                "level2": {"$first": "$category.level2_name"},
                "total": {"$sum": {"$abs": "$amount"}},
                "count": {"$sum": 1},
            }},
            {"$sort": {"total": -1}},
        ]

        try:
            return list(self.col.aggregate(pipeline))
        except Exception as exc:
            logger.error("Erro em aggregate_by_category: %s", exc)
            return []

    def aggregate_by_month(self, year: int) -> list[dict]:
        """
        Soma gastos por mês de um determinado ano.

        Considera apenas débitos não ignorados.

        Returns:
            Lista de dicts com {_id (YYYY-MM), total, count},
            ordenada cronologicamente.
        """
        pipeline = [
            {"$match": {
                "date": {"$regex": f"^{year}-"},
                "type": "debit",
                "is_ignored": False,
            }},
            {"$group": {
                "_id": {"$substr": ["$date", 0, 7]},   # "YYYY-MM"
                "total": {"$sum": {"$abs": "$amount"}},
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]

        try:
            return list(self.col.aggregate(pipeline))
        except Exception as exc:
            logger.error("Erro em aggregate_by_month: %s", exc)
            return []

    def get_period_summary(self, start: datetime, end: datetime) -> dict:
        """
        Retorna totais de débito, crédito e saldo do período.

        Returns:
            Dicionário com total_gasto, total_recebido, saldo e count.
        """
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        pipeline = [
            {"$match": {
                "date": {"$gte": start_str, "$lte": end_str},
                "is_ignored": False,
            }},
            {"$group": {
                "_id": "$type",
                "total": {"$sum": {"$abs": "$amount"}},
                "count": {"$sum": 1},
            }},
        ]

        try:
            results = {row["_id"]: row for row in self.col.aggregate(pipeline)}
            total_gasto = results.get("debit", {}).get("total", 0.0)
            total_recebido = results.get("credit", {}).get("total", 0.0)
            count_debit = results.get("debit", {}).get("count", 0)
            count_credit = results.get("credit", {}).get("count", 0)
            return {
                "total_gasto": total_gasto,
                "total_recebido": total_recebido,
                "saldo": total_recebido - total_gasto,
                "count": count_debit + count_credit,
            }
        except Exception as exc:
            logger.error("Erro em get_period_summary: %s", exc)
            return {"total_gasto": 0.0, "total_recebido": 0.0, "saldo": 0.0, "count": 0}

    def ensure_indexes(self) -> None:
        """
        Cria índices necessários para o funcionamento do repository.

        Deve ser chamado uma vez durante a inicialização do banco
        (scripts/init_db.py). Chamadas subsequentes são idempotentes.
        """
        try:
            # Índice único para deduplicação de importações
            self.col.create_index("dedup_key", unique=True, sparse=True)
            # Índices de performance para buscas comuns
            self.col.create_index([("date", -1)])
            self.col.create_index([("account_id", 1), ("date", -1)])
            self.col.create_index([("categorization.status", 1)])
            self.col.create_index([("category.full_path", 1)])
            logger.info("Índices de 'transactions' criados/verificados.")
        except Exception as exc:
            logger.error("Erro ao criar índices de transactions: %s", exc)


# Singleton
transaction_repo = TransactionRepository()
