"""
vacas gordas 
Script de inicialização do banco de dados
Executa uma única vez para criar coleções, índices e dados iniciais.
"""

import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT
from pymongo.errors import ConnectionFailure, OperationFailure

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.getenv("MONGO_DB",  "vacas_gordas")


# ─── Conexão ────────────────────────────────────────────────────────────────

def get_db():
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        print(f"✅ Conectado ao MongoDB em {MONGO_URI}")
        return client[MONGO_DB]
    except ConnectionFailure:
        print("❌ Não foi possível conectar ao MongoDB.")
        print("   Verifique se o serviço mongod está rodando.")
        sys.exit(1)


# ─── Coleções ────────────────────────────────────────────────────────────────

def create_collections(db):
    existing = db.list_collection_names()
    collections = [
        "accounts",
        "transactions",
        "categories",
        "category_rules",
        "imports",
        "chat_history",
    ]
    for name in collections:
        if name not in existing:
            db.create_collection(name)
            print(f"   + Coleção criada: {name}")
        else:
            print(f"   ~ Coleção já existe: {name}")


# ─── Índices ─────────────────────────────────────────────────────────────────

def create_indexes(db):
    print("\n📑 Criando índices...")

    # accounts
    db.accounts.create_index([("institution", ASCENDING)])
    db.accounts.create_index([("type", ASCENDING)])

    # categories
    db.categories.create_index([("parent_id", ASCENDING)])
    db.categories.create_index([("level", ASCENDING)])
    db.categories.create_index(
        [("full_path", ASCENDING)],
        unique=True,
        name="unique_full_path"
    )

    # transactions
    db.transactions.create_index([("account_id", ASCENDING), ("date", DESCENDING)])
    db.transactions.create_index([("date", DESCENDING)])
    db.transactions.create_index([("categorization.status", ASCENDING)])
    db.transactions.create_index([("category.full_path", ASCENDING)])
    db.transactions.create_index(
        [("description_normalized", TEXT)],
        name="text_search_description"
    )
    db.transactions.create_index(
        [
            ("account_id",             ASCENDING),
            ("date",                   ASCENDING),
            ("description_normalized", ASCENDING),
            ("amount",                 ASCENDING),
        ],
        unique=True,
        sparse=True,
        name="unique_transaction"
    )

    # category_rules
    db.category_rules.create_index([("priority", DESCENDING)])
    db.category_rules.create_index([("is_active", ASCENDING)])
    db.category_rules.create_index([("pattern",   ASCENDING)])

    # imports
    db.imports.create_index(
        [("file_hash", ASCENDING)],
        unique=True,
        name="unique_file_hash"
    )
    db.imports.create_index([("account_id", ASCENDING), ("imported_at", DESCENDING)])

    # chat_history
    db.chat_history.create_index([("session_id", ASCENDING)])
    db.chat_history.create_index([("created_at", DESCENDING)])

    print("✅ Índices criados com sucesso.")


# ─── Categorias padrão ───────────────────────────────────────────────────────

CATEGORY_TREE = {
    "Alimentação":  ["Restaurantes", "Delivery", "Mercado", "Padaria / Café"],
    "Transporte":   ["Combustível", "Uber / 99", "Estacionamento", "Transporte Público"],
    "Moradia":      ["Aluguel", "Condomínio", "Contas (água, luz, gás)", "Manutenção"],
    "Saúde":        ["Farmácia", "Consultas", "Plano de Saúde", "Exames"],
    "Educação":     ["Cursos", "Livros", "Assinaturas Educacionais"],
    "Lazer":        ["Streaming", "Eventos", "Viagens"],
    "Vestuário":    ["Roupas", "Calçados"],
    "Finanças":     ["Investimentos", "Seguros", "Tarifas Bancárias", "Empréstimos"],
}

def create_categories(db):
    print("\n🗂️  Inserindo categorias padrão...")
    now = datetime.now(timezone.utc)
    inserted = 0

    for level1_name, children in CATEGORY_TREE.items():

        # Nível 1
        existing = db.categories.find_one({"full_path": level1_name})
        if existing:
            level1_id = existing["_id"]
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

    print(f"✅ {inserted} categorias inseridas.")


# ─── Verificação final ───────────────────────────────────────────────────────

def verify(db):
    print("\n🔍 Verificação final:")
    for name in ["accounts", "transactions", "categories",
                 "category_rules", "imports", "chat_history"]:
        count = db[name].count_documents({})
        indexes = len(db[name].index_information())
        print(f"   {name:20s} | docs: {count:4d} | índices: {indexes}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  vacas gordas — Inicialização do Banco de Dados")
    print("=" * 50)

    db = get_db()

    print("\n📦 Criando coleções...")
    create_collections(db)

    create_indexes(db)
    create_categories(db)
    verify(db)

    print("\n✅ Banco inicializado com sucesso! Pronto para uso.")
    print("=" * 50)


if __name__ == "__main__":
    main()