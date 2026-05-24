"""Wrapper da biblioteca ofxparse para leitura de arquivos OFX/QFX.

Suporta OFX 1.x (SGML, formato usado pelo Banco do Brasil) e OFX 2.x (XML).
Trata encoding cp1252 automaticamente, que é o padrão dos bancos brasileiros.

Função principal: read_ofx_bytes(raw_content) → OfxReadResult
"""
from __future__ import annotations

import html
import io
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

logger = logging.getLogger(__name__)

# Tipos OFX que representam débito (saída de dinheiro)
_DEBIT_TRNTYPES = frozenset({
    "DEBIT", "CHECK", "ATM", "POS", "PAYMENT", "FEE", "SRVCHG",
})


# ─── Dataclasses de resultado ─────────────────────────────────────────────────

@dataclass
class OfxTransaction:
    """Transação individual extraída de um arquivo OFX."""

    fitid: str
    """Financial Institution Transaction ID — identificador único no OFX."""

    date: date
    """Data de lançamento da transação."""

    memo: str
    """Descrição/memo da transação (HTML entities já decodificados)."""

    amount: Decimal
    """Valor com sinal OFX: negativo = débito, positivo = crédito."""

    trntype: str
    """Tipo OFX em maiúsculas: DEBIT, CREDIT, CHECK, INT, etc."""

    checknum: str | None = None
    """Número do cheque (quando aplicável)."""


@dataclass
class OfxReadResult:
    """Resultado completo da leitura de um arquivo OFX."""

    account_id: str | None
    """Identificador da conta no banco."""

    account_type: str | None
    """Tipo de conta: CHECKING, SAVINGS, CREDITLINE, etc."""

    currency: str
    """Moeda das transações (ISO 4217, ex: BRL)."""

    transactions: list[OfxTransaction] = field(default_factory=list)
    """Lista de transações extraídas."""

    balance: Decimal | None = None
    """Saldo da conta (quando disponível no arquivo)."""

    balance_date: date | None = None
    """Data de referência do saldo."""


# ─── Função principal ─────────────────────────────────────────────────────────

def read_ofx_bytes(raw_content: bytes) -> OfxReadResult:
    """
    Lê bytes OFX/QFX usando a biblioteca ofxparse.

    Tenta três abordagens em ordem para lidar com diferentes encodings e
    versões do formato OFX:
    1. BytesIO direto (OFX 2.x XML ou OFX 1.x com encoding ASCII).
    2. StringIO com decode cp1252 (OFX 1.x do Banco do Brasil).
    3. StringIO com decode utf-8 (fallback geral).

    Returns:
        OfxReadResult com as transações extraídas, ou resultado vazio em falha.
    """
    try:
        from ofxparse import OfxParser  # import tardio: isola dependência opcional
    except ImportError:
        logger.error(
            "Biblioteca 'ofxparse' não instalada. "
            "Execute: poetry add ofxparse"
        )
        return OfxReadResult(account_id=None, account_type=None, currency="BRL")

    ofx = None
    parse_errors: list[str] = []

    # Tentativas de parsing em ordem de prioridade
    attempts = [
        ("BytesIO", lambda: OfxParser.parse(io.BytesIO(raw_content))),
        ("StringIO cp1252", lambda: OfxParser.parse(
            io.StringIO(raw_content.decode("cp1252", errors="replace"))
        )),
        ("StringIO utf-8", lambda: OfxParser.parse(
            io.StringIO(raw_content.decode("utf-8", errors="replace"))
        )),
    ]

    for label, parse_fn in attempts:
        try:
            ofx = parse_fn()
            logger.debug("OFX parseado com sucesso via %s", label)
            break
        except Exception as exc:
            parse_errors.append(f"{label}: {exc}")
            continue

    if ofx is None:
        logger.error(
            "Falha ao parsear arquivo OFX. Tentativas: %s",
            " | ".join(parse_errors),
        )
        return OfxReadResult(account_id=None, account_type=None, currency="BRL")

    # ── Extrai metadados da conta ──────────────────────────────────────────────
    account_id = None
    account_type = None
    currency = "BRL"
    balance: Decimal | None = None
    balance_date: date | None = None

    try:
        account = ofx.account
        account_id = str(getattr(account, "account_id", "") or "").strip() or None
        account_type = str(getattr(account, "account_type", "") or "").strip() or None

        statement = getattr(account, "statement", None)
        if statement:
            raw_currency = getattr(statement, "currency", "BRL")
            currency = str(raw_currency).upper() if raw_currency else "BRL"

            # Saldo
            raw_balance = getattr(statement, "balance", None)
            if raw_balance is not None:
                try:
                    balance = Decimal(str(raw_balance))
                except Exception:
                    pass

            # Data do saldo
            raw_bal_date = getattr(statement, "balance_date", None)
            if raw_bal_date is not None:
                if hasattr(raw_bal_date, "date"):
                    balance_date = raw_bal_date.date()
                elif isinstance(raw_bal_date, date):
                    balance_date = raw_bal_date

    except Exception as exc:
        logger.warning("Erro ao extrair metadados da conta OFX: %s", exc)

    # ── Extrai transações ──────────────────────────────────────────────────────
    transactions: list[OfxTransaction] = []

    try:
        raw_txs = ofx.account.statement.transactions
    except AttributeError:
        logger.warning("Arquivo OFX sem bloco de transações (statement vazio).")
        raw_txs = []

    for idx, raw_tx in enumerate(raw_txs):
        try:
            transactions.append(_parse_ofx_transaction(raw_tx))
        except Exception as exc:
            logger.error(
                "Transação OFX índice %d ignorada: %s", idx, exc, exc_info=True
            )

    logger.info(
        "OFX lido: %d transações / conta '%s' / moeda %s",
        len(transactions), account_id, currency,
    )

    return OfxReadResult(
        account_id=account_id,
        account_type=account_type,
        currency=currency,
        transactions=transactions,
        balance=balance,
        balance_date=balance_date,
    )


def ofx_amount_to_type(amount: Decimal, trntype: str) -> tuple[Decimal, str]:
    """
    Converte o valor OFX (com sinal) para (valor_absoluto, "debit"|"credit").

    Regra primária: sinal do amount (OFX usa negativo para débito).
    Regra secundária: TRNTYPE (quando amount == 0).

    Returns:
        Tupla (Decimal positivo, "debit" | "credit").
    """
    if amount < 0:
        return -amount, "debit"
    elif amount > 0:
        return amount, "credit"
    else:
        # amount == 0: usa TRNTYPE como desempate
        tx_type = "debit" if trntype.upper() in _DEBIT_TRNTYPES else "credit"
        return Decimal("0"), tx_type


# ─── Funções internas ─────────────────────────────────────────────────────────

def _parse_ofx_transaction(tx) -> OfxTransaction:
    """Converte um objeto de transação ofxparse para OfxTransaction."""
    # ID
    fitid = str(getattr(tx, "id", "") or "").strip()

    # Data
    date_raw = getattr(tx, "date", None)
    if hasattr(date_raw, "date"):
        tx_date = date_raw.date()
    elif isinstance(date_raw, date):
        tx_date = date_raw
    else:
        raise ValueError(f"Data inválida na transação OFX: {date_raw!r}")

    # Memo (decodifica HTML entities — comum em Banco do Brasil)
    memo_raw = str(getattr(tx, "memo", "") or "").strip()
    memo = html.unescape(memo_raw)

    # Valor
    amount_raw = getattr(tx, "amount", 0) or 0
    amount = Decimal(str(amount_raw))

    # Tipo
    trntype = str(getattr(tx, "type", "") or "").upper()

    # Cheque (opcional)
    checknum_raw = str(getattr(tx, "checknum", "") or "").strip()
    checknum = checknum_raw if checknum_raw else None

    return OfxTransaction(
        fitid=fitid,
        date=tx_date,
        memo=memo,
        amount=amount,
        trntype=trntype,
        checknum=checknum,
    )
