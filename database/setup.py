"""Módulo de inicialização e gerenciamento do banco de dados.

Contém toda a lógica de setup do MongoDB: criação de coleções,
índices e dados iniciais (categorias). É importável por qualquer
entry point — CLI (scripts/init_db.py) ou UI (ui/pages/settings_page.py).

Funções principais:
    run_full_init(db, reset=False) -> dict   — orquestra tudo, retorna relatório
    reset_categories(db) -> dict             — reseta APENAS as categorias
    get_status(db) -> dict                   — estado atual das coleções
    reset_database(db) -> dict               — apaga tudo e re-inicializa
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.database import Database

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

# ── Árvore de categorias ──────────────────────────────────────────────────────
# Estrutura: (nome, descrição, filhos)
# Suporta profundidade arbitrária.

_CATEGORY_TREE: list[tuple] = [
    ("Receita", "Entradas de dinheiro", [
        ("Akauã", "Salário e rendimentos Akauã", []),
        ("Restituição IR", "Restituição do imposto de renda", []),
    ]),
    ("Despesa", "Saídas de dinheiro", [
        ("Alimentação", "Gastos com alimentação", [
            ("Delivery", "Pedidos por aplicativo (iFood, etc.)", []),
            ("Mercado", "Compras em supermercado", []),
            ("Açougue", "Compras em açougue", []),
        ]),
        ("Finanças", "Movimentações financeiras", [
            ("Investimentos", "Aportes e rendimentos de investimentos", []),
            ("Tarifas bancárias", "Tarifas e encargos bancários", []),
        ]),
        ("Assinaturas", "Serviços de assinatura recorrentes", [
            ("Streaming", "Netflix, Spotify, Disney+, etc.", []),
        ]),
        ("Moradia", "Gastos com moradia", [
            ("Aluguel", "Aluguel do imóvel", []),
            ("Condomínio", "Taxa de condomínio", []),
            ("Contas", "Contas de serviços essenciais", [
                ("Água", "", []),
                ("Luz", "", []),
                ("Internet", "", []),
                ("Gás", "", []),
            ]),
            ("Manutenção", "Reparos e manutenção do imóvel", []),
        ]),
        ("Bem-estar", "Saúde, higiene e vestuário", [
            ("Plano de saúde", "Mensalidade do plano de saúde", []),
            ("Farmácia", "Medicamentos e produtos farmacêuticos", []),
            ("Vestuário", "Roupas, calçados e acessórios", []),
        ]),
        ("Transporte", "Gastos com transporte cotidiano", [
            ("Transporte público", "Ônibus, metrô, trem", []),
            ("Uber", "Corridas de aplicativo", []),
        ]),
        ("Rolezinho", "Lazer e saídas", [
            ("Bar", "Bares e botequins", []),
            ("Restaurante", "Refeições em restaurantes", []),
        ]),
        ("Viagens", "Gastos com viagens e férias", [
            ("Hospedagem", "Hotéis, hostels e Airbnb", []),
            ("Passagem", "Passagens aéreas e terrestres", []),
            ("Carro", "Gastos com carro em viagens", [
                ("Aluguel", "Aluguel de carro", []),
                ("Combustível", "Gasolina e etanol", []),
                ("Pedágio", "Pedágios em rodovias", []),
            ]),
        ]),
    ]),
]


# ── API pública ───────────────────────────────────────────────────────────────

def run_full_init(db: Database, reset: bool = False) -> dict:
    """
    Orquestra a inicialização completa do banco de dados.

    Idempotente quando reset=False: pode ser executado múltiplas vezes com
    segurança, criando apenas o que ainda não existe.

    Args:
        db:    Instância do banco MongoDB.
        reset: Se True, apaga todas as coleções antes de recriar.

    Returns:
        Relatório com collections_created, categories_inserted, status, errors.
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

    return {
        "collections_created": collections_created,
        "categories_inserted": categories_inserted,
        "status": get_status(db),
        "errors": errors,
    }


def reset_categories(db: Database) -> dict:
    """
    Reseta APENAS a coleção de categorias, preservando todos os outros dados.

    Remove todos os documentos de `categories`, recria os índices necessários
    e re-insere a árvore padrão.

    Returns:
        {"dropped": bool, "inserted": int, "errors": list}
    """
    errors: list[str] = []

    try:
        db.drop_collection("categories")
        db.create_collection("categories")
        logger.info("Coleção 'categories' resetada.")
    except Exception as exc:
        errors.append(f"Erro ao resetar coleção: {exc}")
        return {"dropped": False, "inserted": 0, "errors": errors}

    # Recria índices da coleção
    try:
        db.categories.create_index([("parent_id", ASCENDING)])
        db.categories.create_index([("level", ASCENDING)])
        db.categories.create_index(
            [("full_path", ASCENDING)],
            unique=True,
            name="unique_full_path",
        )
    except Exception as exc:
        errors.append(f"Erro ao criar índices: {exc}")

    inserted = 0
    try:
        inserted = _create_categories(db)
    except Exception as exc:
        errors.append(f"Erro ao inserir categorias: {exc}")

    logger.info("reset_categories: %d categorias inseridas.", inserted)
    return {"dropped": True, "inserted": inserted, "errors": errors}


def get_status(db: Database) -> dict:
    """
    Retorna o estado atual das coleções do banco.

    Returns:
        db_name, collections (list de {name, doc_count, index_count}), total_docs.
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
            collections_info.append({"name": name, "doc_count": -1, "index_count": -1})

    return {
        "db_name": db.name,
        "collections": collections_info,
        "total_docs": total_docs,
    }


def reset_database(db: Database) -> dict:
    """Apaga todas as coleções e re-inicializa. Atalho para run_full_init(reset=True)."""
    return run_full_init(db, reset=True)


# ── Funções internas ──────────────────────────────────────────────────────────

def _drop_all_collections(db: Database) -> None:
    for name in _COLLECTIONS:
        try:
            db.drop_collection(name)
            logger.info("Coleção '%s' removida.", name)
        except Exception as exc:
            logger.warning("Erro ao remover coleção '%s': %s", name, exc)


def _create_collections(db: Database) -> list[str]:
    existing = set(db.list_collection_names())
    created = []
    for name in _COLLECTIONS:
        if name not in existing:
            db.create_collection(name)
            created.append(name)
            logger.info("Coleção criada: %s", name)
    return created


def _create_indexes(db: Database) -> None:
    """Cria todos os índices. Idempotente."""
    db.accounts.create_index([("institution", ASCENDING)])
    db.accounts.create_index([("type", ASCENDING)])

    db.categories.create_index([("parent_id", ASCENDING)])
    db.categories.create_index([("level", ASCENDING)])
    db.categories.create_index(
        [("full_path", ASCENDING)], unique=True, name="unique_full_path"
    )

    db.transactions.create_index([("account_id", ASCENDING), ("date", DESCENDING)])
    db.transactions.create_index([("date", DESCENDING)])
    db.transactions.create_index([("categorization.status", ASCENDING)])
    db.transactions.create_index([("category.full_path", ASCENDING)])
    db.transactions.create_index(
        [("description_normalized", TEXT)], name="text_search_description"
    )
    db.transactions.create_index(
        [
            ("account_id", ASCENDING),
            ("date", ASCENDING),
            ("description_normalized", ASCENDING),
            ("amount", ASCENDING),
        ],
        unique=True,
        sparse=True,
        name="unique_transaction",
    )

    db.category_rules.create_index([("priority", DESCENDING)])
    db.category_rules.create_index([("is_active", ASCENDING)])
    db.category_rules.create_index([("pattern", ASCENDING)])

    db.imports.create_index(
        [("file_hash", ASCENDING)], unique=True, name="unique_file_hash"
    )
    db.imports.create_index([("account_id", ASCENDING), ("imported_at", DESCENDING)])

    db.chat_history.create_index([("session_id", ASCENDING)])
    db.chat_history.create_index([("created_at", DESCENDING)])

    logger.info("Índices criados/verificados com sucesso.")


def _create_categories(db: Database) -> int:
    """
    Insere recursivamente a árvore de categorias padrão.

    Cada nó tem: name, description, level, parent_id, full_path, is_active.
    Idempotente — não duplica categorias com o mesmo full_path.

    Returns:
        Número de categorias inseridas nesta execução.
    """
    now = datetime.now(timezone.utc)
    total_inserted = [0]  # uso de lista para mutação em closure

    def _insert(nodes: list[tuple], parent_id=None, parent_path: str = "", level: int = 1) -> None:
        for name, description, children in nodes:
            full_path = f"{parent_path} > {name}" if parent_path else name

            existing = db.categories.find_one({"full_path": full_path})
            if existing:
                cat_id = existing["_id"]
            else:
                result = db.categories.insert_one({
                    "name":        name,
                    "description": description,
                    "level":       level,
                    "parent_id":   parent_id,
                    "full_path":   full_path,
                    "color":       None,
                    "icon":        None,
                    "is_active":   True,
                    "created_at":  now,
                })
                cat_id = result.inserted_id
                total_inserted[0] += 1

            if children:
                _insert(children, cat_id, full_path, level + 1)

    _insert(_CATEGORY_TREE)
    logger.info("%d categoria(s) inserida(s).", total_inserted[0])
    return total_inserted[0]
