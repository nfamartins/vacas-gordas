"""Gerenciamento de conexão singleton com o MongoDB.

Usa o padrão de classe com estado de classe para garantir uma única conexão
reutilizada em toda a aplicação, inclusive entre reexecuções do Streamlit.
"""
from __future__ import annotations

import logging

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from config.settings import settings

logger = logging.getLogger(__name__)


class MongoConnection:
    """Gerencia a conexão singleton com o MongoDB."""

    _client: MongoClient | None = None
    _db: Database | None = None

    @classmethod
    def get_db(cls) -> Database:
        """
        Retorna a instância do banco de dados.

        Inicializa a conexão na primeira chamada; reutiliza nas seguintes.
        """
        if cls._db is None:
            cls._connect()
        return cls._db  # type: ignore[return-value]

    @classmethod
    def _connect(cls) -> None:
        """
        Estabelece a conexão com o MongoDB.

        Timeout de 3 segundos para falhar rápido se o mongod não estiver rodando.

        Raises:
            ConnectionError: com mensagem amigável se a conexão falhar.
        """
        try:
            cls._client = MongoClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=3_000,
            )
            # Verifica conectividade imediatamente via ping
            cls._client.admin.command("ping")
            cls._db = cls._client[settings.mongo_db]
            logger.info(
                "✅ Conectado ao MongoDB — %s / banco: %s",
                settings.mongo_uri,
                settings.mongo_db,
            )
        except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
            msg = (
                f"❌ Não foi possível conectar ao MongoDB em '{settings.mongo_uri}'.\n"
                "Verifique se o serviço MongoDB está em execução (mongod) e tente novamente."
            )
            logger.critical(msg)
            raise ConnectionError(msg) from exc

    @classmethod
    def close(cls) -> None:
        """
        Fecha a conexão e libera os recursos.

        Útil em testes ou ao encerrar a aplicação de forma controlada.
        """
        if cls._client:
            cls._client.close()
            cls._client = None
            cls._db = None
            logger.debug("Conexão MongoDB encerrada.")


def get_db() -> Database:
    """Atalho para MongoConnection.get_db() — usado pelos repositories."""
    return MongoConnection.get_db()
