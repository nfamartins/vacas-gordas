"""Parser para arquivos CSV do C6 Bank.

Suporta dois tipos de arquivo exportados pelo C6 Bank:

1. **Fatura do cartão de crédito** (crédito):
   Colunas: Data, Descrição, Valor
   Convenção: valor positivo = despesa do usuário (debit)
              valor negativo = estorno ou pagamento da fatura (credit)

2. **Extrato da conta corrente** (checking):
   Colunas: Data, Histórico, Descrição, Valor, Saldo
   Convenção: valor positivo = crédito (depósito, PIX recebido)
              valor negativo = débito (pagamento, PIX enviado)

A detecção do tipo é automática, baseada nas colunas presentes no CSV.
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

# Colunas normalizadas obrigatórias para cada tipo de arquivo
_FATURA_COLS: frozenset[str] = frozenset({"data", "descricao", "valor"})
_EXTRATO_COLS: frozenset[str] = frozenset({"data", "historico", "descricao", "valor", "saldo"})

# Descritores que indicam linha de totalizador/saldo — devem ser ignorados
_SKIP_HISTORICO = frozenset({
    "saldo anterior", "saldo atual", "saldo disponivel",
    "saldo", "total", "limite",
})


class C6CsvParser(BaseParser):
    """
    Parser para arquivos CSV do C6 Bank (fatura e extrato).

    Detecção automática: verifica se as colunas do CSV correspondem ao padrão
    de fatura ou de extrato do C6. Aceita também arquivos onde o nome contém "c6".
    """

    parser_id = "c6_csv"
    institution = "C6 Bank"

    @classmethod
    def can_parse(cls, filename: str, raw_content: bytes) -> bool:
        """
        Retorna True se o arquivo é reconhecido como CSV do C6 Bank.

        Critérios (qualquer um é suficiente):
        1. Nome do arquivo contém "c6" (ex: "c6_fatura_maio.csv").
        2. As colunas do CSV correspondem ao padrão de fatura ou extrato C6.
        """
        if not filename.lower().endswith(".csv"):
            return False

        # Critério 1: nome do arquivo
        filename_match = bool(re.search(r"\bc6\b|c6bank|c6-bank", filename, re.IGNORECASE))

        # Critério 2: colunas do CSV
        try:
            headers = peek_headers(raw_content)
            norm_headers = {normalize_col_name(h) for h in headers}
            header_match = (
                _FATURA_COLS.issubset(norm_headers)
                or _EXTRATO_COLS.issubset(norm_headers)
            )
        except Exception:
            header_match = False

        return filename_match or header_match

    def parse(self, filename: str, raw_content: bytes) -> list[RawTransaction]:
        """Extrai transações do CSV C6 Bank, detectando tipo automaticamente."""
        result = read_csv_bytes(raw_content)

        if result.dataframe.empty:
            logger.warning("C6 CSV '%s' está vazio ou não pôde ser lido.", filename)
            return []

        df = result.dataframe
        file_type = self._detect_file_type(list(df.columns))

        if file_type is None:
            logger.error(
                "C6 CSV '%s': colunas não reconhecidas como fatura ou extrato. "
                "Colunas encontradas: %s",
                filename, list(df.columns),
            )
            return []

        logger.info("C6 CSV '%s' identificado como: %s", filename, file_type.upper())

        if file_type == "fatura":
            return self._parse_fatura(df, filename)
        return self._parse_extrato(df, filename)

    # ── Métodos internos ──────────────────────────────────────────────────────

    def _detect_file_type(self, headers: list[str]) -> str | None:
        """
        Retorna 'fatura', 'extrato' ou None.

        Extrato é verificado primeiro pois é um superconjunto das colunas da fatura
        (ambos têm Data/Descrição/Valor — o Saldo é o diferencial do extrato).
        """
        norm = {normalize_col_name(h) for h in headers}
        if _EXTRATO_COLS.issubset(norm):
            return "extrato"
        if _FATURA_COLS.issubset(norm):
            return "fatura"
        return None

    def _find_col(self, df: pd.DataFrame, norm_target: str) -> str | None:
        """Retorna o nome original da coluna cujo nome normalizado é `norm_target`."""
        for col in df.columns:
            if normalize_col_name(col) == norm_target:
                return col
        return None

    def _parse_fatura(self, df: pd.DataFrame, filename: str) -> list[RawTransaction]:
        """
        Parseia fatura do cartão C6 Bank.

        Coluna "Valor":
        - Positivo → despesa do usuário → type="debit"
        - Negativo → estorno ou pagamento → type="credit"
        """
        col_data = self._find_col(df, "data")
        col_desc = self._find_col(df, "descricao")
        col_valor = self._find_col(df, "valor")

        if not all([col_data, col_desc, col_valor]):
            logger.error(
                "C6 fatura '%s': colunas obrigatórias ausentes "
                "(esperado: Data, Descrição, Valor). Encontradas: %s",
                filename, list(df.columns),
            )
            return []

        transactions: list[RawTransaction] = []

        for idx, row in df.iterrows():
            try:
                date_raw = str(row[col_data]).strip()
                desc_raw = str(row[col_desc]).strip()
                valor_raw = str(row[col_valor]).strip()

                # Pula linhas com campos essenciais ausentes ou cabeçalho repetido
                if _is_empty(date_raw) or _is_empty(desc_raw) or _is_empty(valor_raw):
                    continue
                if date_raw.lower() == "data":
                    continue  # linha de cabeçalho repetida no meio do arquivo

                date_str = parse_br_date(date_raw)
                amount_signed = parse_br_amount(valor_raw)

                if amount_signed >= 0:
                    tx_type: Literal["debit", "credit"] = "debit"
                    amount_abs = amount_signed
                else:
                    tx_type = "credit"
                    amount_abs = -amount_signed

                transactions.append(RawTransaction(
                    date=date_str,
                    description=desc_raw,
                    amount=amount_abs,
                    type=tx_type,
                    raw_text=f"{date_raw}|{desc_raw}|{valor_raw}",
                ))

            except Exception as exc:
                logger.error(
                    "C6 fatura '%s', linha %s ignorada: %s",
                    filename, idx, exc, exc_info=True,
                )

        logger.info(
            "C6 fatura '%s': %d transação(ões) extraída(s).",
            filename, len(transactions),
        )
        return transactions

    def _parse_extrato(self, df: pd.DataFrame, filename: str) -> list[RawTransaction]:
        """
        Parseia extrato da conta corrente C6 Bank.

        Coluna "Valor":
        - Positivo → crédito (depósito, PIX recebido)
        - Negativo → débito (pagamento, PIX enviado)
        """
        col_data = self._find_col(df, "data")
        col_hist = self._find_col(df, "historico")   # pode ser None em versões antigas
        col_desc = self._find_col(df, "descricao")
        col_valor = self._find_col(df, "valor")

        if not all([col_data, col_valor]):
            logger.error(
                "C6 extrato '%s': colunas obrigatórias ausentes "
                "(esperado: Data, Valor). Encontradas: %s",
                filename, list(df.columns),
            )
            return []

        transactions: list[RawTransaction] = []

        for idx, row in df.iterrows():
            try:
                date_raw = str(row[col_data]).strip()
                valor_raw = str(row[col_valor]).strip()
                hist_raw = str(row[col_hist]).strip() if col_hist else ""
                desc_raw = str(row[col_desc]).strip() if col_desc else ""

                # Pula linhas inválidas
                if _is_empty(date_raw) or _is_empty(valor_raw):
                    continue
                if date_raw.lower() == "data":
                    continue  # cabeçalho repetido

                # Pula linha de "SALDO ANTERIOR" e similares
                if hist_raw.lower().strip() in _SKIP_HISTORICO:
                    logger.debug("Linha de controle ignorada: '%s'", hist_raw)
                    continue

                # Monta descrição: combina Histórico + Descrição (evita duplicação)
                if hist_raw and desc_raw and hist_raw.lower() != desc_raw.lower():
                    description = f"{hist_raw} - {desc_raw}"
                elif hist_raw:
                    description = hist_raw
                elif desc_raw:
                    description = desc_raw
                else:
                    description = "(sem descrição)"

                date_str = parse_br_date(date_raw)
                amount_signed = parse_br_amount(valor_raw)

                if amount_signed >= 0:
                    tx_type: Literal["debit", "credit"] = "credit"
                    amount_abs = amount_signed
                else:
                    tx_type = "debit"
                    amount_abs = -amount_signed

                transactions.append(RawTransaction(
                    date=date_str,
                    description=description,
                    amount=amount_abs,
                    type=tx_type,
                    raw_text=f"{date_raw}|{hist_raw}|{desc_raw}|{valor_raw}",
                ))

            except Exception as exc:
                logger.error(
                    "C6 extrato '%s', linha %s ignorada: %s",
                    filename, idx, exc, exc_info=True,
                )

        logger.info(
            "C6 extrato '%s': %d transação(ões) extraída(s).",
            filename, len(transactions),
        )
        return transactions


# ─── Utilitários locais ───────────────────────────────────────────────────────

def _is_empty(value: str) -> bool:
    """Retorna True se o valor é vazio, NaN (string) ou traço."""
    return value.lower() in ("", "nan", "-", "n/a", "none")
