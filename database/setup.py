"""Módulo de inicialização e gerenciamento do banco de dados.

Contém toda a lógica de setup do MongoDB: criação de coleções,
índices e dados iniciais (categorias). É importável por qualquer
entry point — CLI (scripts/init_db.py) ou UI (ui/pages/settings_page.py).

Funções principais:
    run_full_init(db, reset=False) -> dict  — orquestra tudo, retorna relatório
    get_status(db) -> dict                  — estado atual das coleções
    reset_database(db)                      — apaga tudo e re-inicializa
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo.database import Database
from pymongo import ASCENDING, DESCENDING, TEXT

logger = logging.getLogger(__name__)

# ── Coleções gerenciadas ──────────────────────────────────────────────────────

_COLLECTIONS = [
    "accounts",
    "transactions",
    "categories",
    "category_rules",
    "imports",
    "chat_history",
]

# ── Árvore de categorias padrão ───────────────────────────────────────────────

_CATEGORY_TREE: dict[str, list[str]] = {
    "Alimentação":  ["Restaurantes", "Delivery", "Mercado", "Padaria / Café"],
    "Transporte":   ["Combustível", "Uber / 99", "Estacionamento", "Transporte Público"],
    "Moradia":      ["Aluguel", "Condomínio", "Contas (água, luz, gás)", "Manutenção"],
    "Saúde":        ["Farmácia", "Consultas", "Plano de Saúde", "Exames"],
    "Educação":     ["Cursos", "Livros", "Assinaturas Educacionais"],
    "Lazer":        ["Streaming", "Eventos", "Viagens"],
    "Vestuário":    ["Roupas", "Calçados"],
    "Finanças":     ["Investimentos", "Seguros", "Tarifas Bancárias", "Empréstimos"],
    "Outros":       ["Não Categorizado"],
}


# ── API pública ───────────────────────────────────────────────────────────────

def run_full_init(db: Database, reset: bool = False) -> dict:
    """
    Orquestra a inicialização completa do banco de dados.

    Idempotente quando reset=False: pode ser executado múltiplas vezes com segurança,
    criando apenas o que ainda não existe.

    Args:
        db:    Instância do banco MongoDB.
        reset: Se True, apaga todas as coleções antes de recriar.

    Returns:
        Relatório com:
        - collections_created: lista de coleções criadas nesta execução
        - categories_inserted: número de categorias inseridas
        - status:              resultado de get_status(db)
        - errors:              lista de erros não-fatais encontrados
    """
    errors: list[str] = []

    if reset:
        logger.warning("⚠️  Reset solicitado — apagando todas as coleções…")
        try:
            _drop_all_collections(db)
        except Exception as exc:
            msg = f"Erro ao resetar coleções: {exc}"
            logger.error(msg)
            errors.append(msg)

    collections_created = _create_collections(db)

    try:
        _create_indexes(db)
    except Exception as exc:
        msg = f"Erro ao criar índices: {exc}"
        logger.error(msg, exc_info=True)
        errors.append(msg)

    categories_inserted = 0
    try:
        categories_inserted = _create_categories(db)
    except Exception as exc:
        msg = f"Erro ao inserir categorias: {exc}"
        logger.error(msg, exc_info=True)
        errors.append(msg)

    status = get_status(db)

    logger.info(
        "Inicialização concluída: %d coleção(ões) criada(s), "
        "%d categoria(s) inserida(s), %d erro(s).",
        len(collections_created), categories_inserted, len(errors),
    )

    return {
        "collections_created": collections_created,
        "categories_inserted": categories_inserted,
        "status": status,
        "errors": errors,
    }


def get_status(db: Database) -> dict:
    """
    Retorna o estado atual das coleções do banco.

    Returns:
        Dicionário com:
        - db_name: nome do banco
        - collections: lista de {name, doc_count, index_count} por coleção
        - total_docs: soma total de documentos
    """
    collections_info = []
    total_docs = 0

    for name in _COLLECTIONS:
        try:
            col = db[name]
            doc_count = col.count_documents({})
            index_count = len(col.index_information())
            collections_info.append({
                "name": name,
                "doc_count": doc_count,
                "index_count": index_count,
            })
            total_docs += doc_count
        except Exception as exc:
            logger.warning("Erro ao obter status de '%s': %s", name, exc)
            collections_info.append({
                "name": name,
                "doc_count": -1,
                "index_count": -1,
            })

    return {
        "db_name": db.name,
        "collections": collections_info,
        "total_docs": total_docs,
    }


def reset_database(db: Database) -> dict:
    """
    Apaga todas as coleções e re-inicializa o banco do zero.

    Atalho para run_full_init(db, reset=True).

    Returns:
        Relatório de run_full_init.
    """
    return run_full_init(db, reset=True)


# ── Funções internas ──────────────────────────────────────────────────────────

def _drop_all_collections(db: Database) -> None:
    """Remove todas as coleções gerenciadas."""
    for name in _COLLECTIONS:
        try:
            db.drop_collection(name)
            logger.info("Coleção '%s' removida.", name)
        except Exception as exc:
            logger.warning("Erro ao remover coleção '%s': %s", name, exc)


def _create_collections(db: Database) -> list[str]:
    """
    Cria coleções que ainda não existem.

    Returns:
        Lista dos nomes das coleções criadas nesta execução.
    """
    existing = set(db.list_collection_names())
    created = []

    for name in _COLLECTIONS:
        if name not in existing:
            db.create_collection(name)
            created.append(name)
            logger.info("Coleção criada: %s", name)
        else:
            logger.debug("Coleção já existe: %s", name)

    return created


def _create_indexes(db: Database) -> None:
    """
    Cria todos os índices necessários. Idempotente — pymongo ignora
    índices já existentes com a mesma definição.
    """
    # ── accounts ──────────────────────────────────────────────────────────────
    db.accounts.create_index([("institution", ASCENDING)])
    db.accounts.create_index([("type", ASCENDING)])

    # ── categories ────────────────────────────────────────────────────────────
    db.categories.create_index([("parent_id", ASCENDING)])
    db.categories.create_index([("level", ASCENDING)])
    db.categories.create_index(
        [("full_path", ASCENDING)],
        unique=True,
        name="unique_full_path",
    )

    # ── transactions ──────────────────────────────────────────────────────────
    db.transactions.create_index([("account_id", ASCENDING), ("date", DESCENDING)])
    db.transactions.create_index([("date", DESCENDING)])
    db.transactions.create_index([("categorization.status", ASCENDING)])
    db.transactions.create_index([("category.full_path", ASCENDING)])
    db.transactions.create_index(
        [("description_normalized", TEXT)],
        name="text_search_description",
    )
    # Índice único composto: garante deduplicação real no banco
    db.transactions.create_index(
        [
            ("account_id",             ASCENDING),
            ("date",                   ASCENDING),
            ("description_normalized", ASCENDING),
            ("amount",                 ASCENDING),
        ],
        unique=True,
        sparse=True,
        name="unique_transaction",
    )

    # ── category_rules ────────────────────────────────────────────────────────
    db.category_rules.create_index([("priority", DESCENDING)])
    db.category_rules.create_index([("is_active", ASCENDING)])
    db.category_rules.create_index([("pattern",   ASCENDING)])

    # ── imports ───────────────────────────────────────────────────────────────
    db.imports.create_index(
        [("file_hash", ASCENDING)],
        unique=True,
        name="unique_file_hash",
    )
    db.imports.create_index([("account_id", ASCENDING), ("imported_at", DESCENDING)])

    # ── chat_history ──────────────────────────────────────────────────────────
    db.chat_history.create_index([("session_id", ASCENDING)])
    db.chat_history.create_index([("created_at", DESCENDING)])

    logger.info("Índices criados/verificados com sucesso.")


def _create_categories(db: Database) -> int:
    """
    Insere as categorias padrão (nível 1 e 2) se ainda não existirem.

    Returns:
        Número de categorias inseridas nesta execução.
    """
    now = datetime.now(timezone.utc)
    inserted = 0

    for level1_name, children in _CATEGORY_TREE.items():
        # Nível 1
        existing_l1 = db.categories.find_one({"full_path": level1_name})
        if existing_l1:
            level1_id = existing_l1["_id"]
        else:
            result = db.categories.insert_one({
                "name":       level1_name,
                "level":      1,
                "parent_id":  None,
                "full_path":  level1_name,
                "color":      None,
                "icon":       None,
                "is_active":  True,
                "created_at": now,
            })
            level1_id = result.inserted_id
            inserted += 1

        # Nível 2
        for level2_name in children:
            full_path = f"{level1_name} > {level2_name}"
            if not db.categories.find_one({"full_path": full_path}):
                db.categories.insert_one({
                    "name":       level2_name,
                    "level":      2,
                    "parent_id":  level1_id,
                    "full_path":  full_path,
                    "color":      None,
                    "icon":       None,
                    "is_active":  True,
                    "created_at": now,
                })
                inserted += 1

    logger.info("%d categoria(s) inserida(s).", inserted)
    return inserted
