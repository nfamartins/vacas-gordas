"""Exporta todos os repositories como singletons prontos para uso."""
from database.repositories.account_repo import AccountRepository, account_repo
from database.repositories.category_repo import CategoryRepository, category_repo
from database.repositories.chat_repo import ChatRepository, chat_repo
from database.repositories.import_repo import ImportRepository, import_repo
from database.repositories.rule_repo import RuleRepository, rule_repo
from database.repositories.transaction_repo import TransactionRepository, transaction_repo

__all__ = [
    # Classes (para tipagem e testes)
    "AccountRepository",
    "CategoryRepository",
    "ChatRepository",
    "ImportRepository",
    "RuleRepository",
    "TransactionRepository",
    # Singletons (para uso direto)
    "account_repo",
    "category_repo",
    "chat_repo",
    "import_repo",
    "rule_repo",
    "transaction_repo",
]
