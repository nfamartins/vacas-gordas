"""Camada de banco de dados — exporta conexão e todos os repositories."""
from database.connection import MongoConnection, get_db
from database.repositories import (
    account_repo,
    category_repo,
    chat_repo,
    import_repo,
    rule_repo,
    transaction_repo,
)

__all__ = [
    # Conexão
    "MongoConnection",
    "get_db",
    # Repositories (singletons)
    "account_repo",
    "category_repo",
    "chat_repo",
    "import_repo",
    "rule_repo",
    "transaction_repo",
]
