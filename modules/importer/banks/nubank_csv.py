"""Parser para arquivos CSV do Nubank.

Suporta dois tipos de exportação:

1. **Fatura do cartão** (crédito):
   Colunas: date, title, amount
   Convenção: amount positivo = despesa (debit)
              amount negativo = pagamento ou estorno (credit)

2. **NuConta** (conta corrente):
   Colunas: date, title, amount (e possivelmente "identifier", "category")
   Convenção: amount positivo = crédito (depósito, PIX recebido)
              amount negativo = débito (pagamento, PIX enviado)

Distinção fatura vs NuConta:
- Ambas têm as mesmas colunas base.
- NuConta pode incluir colunas extras: "identifier", "category", "type".
- Na prática, a convenção de sinal é oposta entre os dois tipos,
  por isso a distinção é importante para evitar débito/crédito invertidos.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Literal

import pandas as pd

from modules.importer.base_parser import (
    BaseParser,
    RawTransaction,
    normalize_col_name,
    parse_br_amount,
    parse_br_date,
)
from modules.importer.csv_reader import peek_headers, read_csv_bytes

logger = logging.getLogger(__name__)

# Assinatura de colunas normalizadas (presença obrigatória)
_BASE_COLS: frozenset[str] = frozenset({"date", "title", "amount"})
# Coluna extra que indica NuConta
_NUCONTA_EXTRA_COLS: frozenset[str] = frozenset({"identifier", "category", "type"})


class NubankCsvParser(BaseParser):
    """
    Parser para arquivos CSV do Nubank (fatura do cartão e NuConta).

    Identificação pelo cabeçalho CSV com colunas {date, title, amount}.
    Essa combinação é característica exclusiva dos exports do Nubank.
    """

    parser_id = "nubank_csv"
    institution = "Nubank"

    @classmethod
    def can_parse(cls, filename: str, raw_content: bytes) -> bool:
        """
        Retorna True se o arquivo é reconhecido como CSV do Nubank.

        Critérios (qualquer um é suficiente):
        1. Nome do arquivo contém "nubank".
        2. Cabeçalho CSV contém as colunas {date, title, amount} (case-insensitive).
        """
        if not filename.lower().endswith(".csv"):
            return False

        # Critério 1: nome do arquivo
        if re.search(r"nubank", filename, re.IGNORECASE):
            return True

        # Critério 2: assinatura de colunas
        try:
            headers = peek_headers(raw_content)
            norm_headers = {normalize_col_name(h) for h in headers}
            return _BASE_COLS.issubset(norm_headers)
        except Exception:
            return False

    def parse(self, filename: str, raw_content: bytes) -> list[RawTransaction]:
        """Extrai transações do CSV Nubank, detectando tipo automaticamente."""
        result = read_csv_bytes(raw_content)

        if result.dataframe.empty:
            logger.warning("Nubank CSV '%s' está vazio ou não pôde ser lido.", filename)
            return []

        df = result.dataframe
        subtype = self._detect_subtype(list(df.columns))

        logger.info("Nubank CSV '%s' identificado como: %s", filename, subtype.upper())

        transactions: list[RawTransaction] = []

        col_date = self._find_col(df, "date")
        col_title = self._find_col(df, "title")
        col_amount = self._find_col(df, "amount")

        if not all([col_date, col_title, col_amount]):
            logger.error(
                "Nubank CSV '%s': colunas obrigatórias ausentes. "
                "Esperado: date, title, amount. Encontradas: %s",
                filename, list(df.columns),
            )
            return []

        for idx, row in df.iterrows():
            try:
                date_raw = str(row[col_date]).strip()
                title_raw = str(row[col_title]).strip()
                amount_raw = str(row[col_amount]).strip()

                if _is_empty(date_raw) or _is_empty(title_raw) or _is_empty(amount_raw):
                    continue

                date_str = self._parse_date(date_raw)
                amount_signed = parse_br_amount(amount_raw)

                tx_type, amount_abs = self._determine_type(amount_signed, subtype)

                transactions.append(RawTransaction(
                    date=date_str,
                    description=title_raw,
                    amount=amount_abs,
                    type=tx_type,
                    raw_text=f"{date_raw}|{title_raw}|{amount_raw}",
                ))

            except Exception as exc:
                logger.error(
                    "Nubank CSV '%s', linha %s ignorada: %s",
                    filename, idx, exc, exc_info=True,
                )

        logger.info(
            "Nubank CSV '%s': %d transação(ões) extraída(s).",
            filename, len(transactions),
        )
        return transactions

    # ── Métodos internos ──────────────────────────────────────────────────────

    def _detect_subtype(self, headers: list[str]) -> str:
        """
        Retorna 'fatura' ou 'nuconta'.

        NuConta inclui colunas extras como "identifier" ou "category".
        Na ausência dessas colunas, assume fatura do cartão.
        """
        norm = {normalize_col_name(h) for h in headers}
        if norm & _NUCONTA_EXTRA_COLS:
            return "nuconta"
        return "fatura"

    def _find_col(self, df: pd.DataFrame, norm_target: str) -> str | None:
        """Retorna o nome original da coluna cujo nome normalizado é `norm_target`."""
        for col in df.columns:
            if normalize_col_name(col) == norm_target:
                return col
        return None

    def _parse_date(self, value: str) -> str:
        """
        Converte data Nubank para YYYY-MM-DD.

        Nubank usa ISO 8601 (YYYY-MM-DD) nos exports CSV,
        mas aceita DD/MM/YYYY como fallback.
        """
        return parse_br_date(value)

    def _determine_type(
        self,
        amount_signed: Decimal,
        subtype: str,
    ) -> tuple[Literal["debit", "credit"], Decimal]:
        """
        Determina o tipo da transação com base no sinal e no subtipo.

        Fatura do cartão:
          - Positivo → despesa do usuário → debit
          - Negativo → pagamento ou estorno → credit

        NuConta:
          - Positivo → depósito/PIX recebido → credit
          - Negativo → pagamento/PIX enviado → debit
        """
        if subtype == "fatura":
            if amount_signed >= 0:
                return "debit", amount_signed
            else:
                return "credit", -amount_signed
        else:
            # nuconta
            if amount_signed >= 0:
                return "credit", amount_signed
            else:
                return "debit", -amount_signed


# ─── Utilitários locais ───────────────────────────────────────────────────────

def _is_empty(value: str) -> bool:
    """Retorna True se o valor é vazio, NaN ou traço."""
    return value.lower() in ("", "nan", "-", "n/a", "none")
