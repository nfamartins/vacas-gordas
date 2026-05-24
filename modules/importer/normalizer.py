"""Normalização de RawTransaction para o schema de documento MongoDB.

Responsabilidades:
- normalize_description(): limpa o texto para deduplicação e busca.
- compute_dedup_key(): gera hash SHA-256 para detecção de duplicatas.
- build_transaction_document(): monta o documento completo pronto para inserção.

Convenção de sinal no documento MongoDB:
  Débito  (saída de dinheiro) → amount NEGATIVO  (ex: -45.90)
  Crédito (entrada de dinheiro) → amount POSITIVO (ex: +200.00)
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal

from bson import ObjectId

from modules.importer.base_parser import RawTransaction

logger = logging.getLogger(__name__)


# ─── Funções públicas ─────────────────────────────────────────────────────────

def normalize_description(text: str) -> str:
    """
    Normaliza texto de descrição para uso em deduplicação e busca textual.

    Transformações aplicadas (nesta ordem):
    1. Lowercase.
    2. Decomposição NFD: separa letras de acentos.
    3. Remove combining characters (acentos, cedilha, til, etc.).
    4. Remove caracteres especiais — mantém apenas letras, dígitos e espaço.
    5. Colapsa múltiplos espaços em um.
    6. Strip nas extremidades.

    Exemplos:
        "IFOOD*RESTAURANTE ABC SP 20/05" → "ifood restaurante abc sp 20 05"
        "Pagamento de Boleto - Água/Luz"  → "pagamento de boleto agua luz"
    """
    # NFD: "é" → "e" + combining_accent
    nfd = unicodedata.normalize("NFD", text.lower())
    # Remove combining characters (categoria Unicode "Mn" = Mark, Nonspacing)
    without_accents = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    # Mantém apenas letras ASCII, dígitos e espaço
    clean = re.sub(r"[^a-z0-9 ]", " ", without_accents)
    # Colapsa espaços múltiplos
    return re.sub(r"\s+", " ", clean).strip()


def compute_dedup_key(
    account_id: ObjectId,
    date_str: str,
    description_normalized: str,
    amount: Decimal,
) -> str:
    """
    Calcula a chave de deduplicação como hash SHA-256.

    A chave é gerada a partir da tupla:
        (account_id, date, description_normalized, amount_absoluto)

    Usar amount absoluto (sem sinal) garante que o mesmo lançamento não seja
    duplicado independente de como cada banco representa o sinal.

    Returns:
        String hexadecimal de 64 caracteres.
    """
    raw = f"{account_id}|{date_str}|{description_normalized}|{amount}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_transaction_document(
    raw: RawTransaction,
    account_id: ObjectId,
    import_id: ObjectId,
    source_file: str,
    parser_id: str,
) -> dict:
    """
    Constrói o documento MongoDB completo a partir de uma RawTransaction.

    O documento segue exatamente o schema definido em database/schemas.md,
    com os seguintes campos adicionais para uso interno:
    - `dedup_key`: hash SHA-256 para detecção eficiente de duplicatas.
    - `raw.parser_id`: identifica qual parser gerou a transação.

    Args:
        raw:         Transação bruta validada pelo parser.
        account_id:  ObjectId da conta associada (já existe no MongoDB).
        import_id:   ObjectId do registro de importação em curso.
        source_file: Nome do arquivo-fonte (para rastreabilidade).
        parser_id:   Identificador do parser (ex: "c6_csv", "nubank_pdf").

    Returns:
        Dicionário Python pronto para pymongo.insert_one() / insert_many().
    """
    if raw.amount == 0:
        logger.warning(
            "Transação com valor zero: '%s' em %s (parser: %s). "
            "Incluída mesmo assim (pode ser isenção de tarifa).",
            raw.description, raw.date, parser_id,
        )

    desc_normalized = normalize_description(raw.description)

    # Valor com sinal: débito → negativo, crédito → positivo
    signed_amount = float(-raw.amount if raw.type == "debit" else raw.amount)

    dedup_key = compute_dedup_key(account_id, raw.date, desc_normalized, raw.amount)

    now = datetime.now(timezone.utc)

    return {
        "_id": ObjectId(),
        "account_id": account_id,
        "import_id": import_id,
        # ── Dados da transação ─────────────────────────────────────────────
        "date": raw.date,
        "description": raw.description,
        "description_normalized": desc_normalized,
        "amount": signed_amount,
        "type": raw.type,
        "currency": raw.currency,
        # ── Categorização (vazia, aguarda rule_engine / LLM) ──────────────
        "category": {
            "level1_id": None,
            "level1_name": None,
            "level2_id": None,
            "level2_name": None,
            "full_path": None,
        },
        "categorization": {
            "status": "pending",
            "method": None,
            "llm_suggestion": None,
            "llm_confidence": None,
            "rule_id": None,
            "confirmed_by_user": False,
            "confirmed_at": None,
        },
        # ── Metadados ──────────────────────────────────────────────────────
        "tags": [],
        "notes": None,
        "is_ignored": False,
        "is_transfer": False,
        "duplicate_of": None,
        # ── Dados brutos (rastreabilidade) ─────────────────────────────────
        "raw": {
            "original_description": raw.description,
            "source_file": source_file,
            "raw_text": raw.raw_text,
            "parser_id": parser_id,
        },
        # ── Controle interno ───────────────────────────────────────────────
        "dedup_key": dedup_key,
        "created_at": now,
        "updated_at": now,
    }
