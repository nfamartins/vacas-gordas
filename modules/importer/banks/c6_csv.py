"""Parser para arquivos CSV do C6 Bank.

Suporta dois tipos de arquivo exportados pelo C6 Bank:

1. **Fatura do cartão de crédito**:
   Colunas: Data de Compra, Descrição, Parcela, Valor (em R$), ...
   Convenção: valor positivo = despesa (debit)
              valor negativo = estorno ou pagamento da fatura (credit)

2. **Extrato da conta corrente**:
   Colunas: Data Lançamento, Data Contábil, Título, Descrição,
            Entrada(R$), Saída(R$), Saldo do Dia(R$)
   O extrato pode ter um preâmbulo informativo antes do cabeçalho real
   (título, agência, período) — detectado e ignorado automaticamente.
   Convenção: Entrada(R$) > 0 → credit; Saída(R$) > 0 → debit.

A detecção do tipo é automática a partir das colunas presentes no CSV.
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

# Descritores que indicam linha de totalizador/saldo — devem ser ignorados
_SKIP_HISTORICO = frozenset({
    "saldo anterior", "saldo atual", "saldo disponivel",
    "saldo", "total", "limite",
})

# Padrões que indicam pagamento de fatura de cartão (extrato e fatura)
_CC_PAYMENT_KEYWORDS = (
    "pgto fat", "fatura de cart", "fat cartao", "fat. cartao",
    "pagamento fatura", "pagto fatura",
)

# Padrões que indicam o lançamento de pagamento DA fatura (dentro da fatura)
# Ex: "Inclusao de Pagamento" no C6 Bank — é o crédito da conta corrente no cartão
_FATURA_PAYMENT_KEYWORDS = (
    "inclusao de pagamento",
    "pagamento recebido",
    "credito de pagamento",
    "pag recebido",
)


class C6CsvParser(BaseParser):
    """
    Parser para arquivos CSV do C6 Bank (fatura e extrato).

    Detecção automática: verifica colunas do CSV para identificar o tipo.
    Aceita também arquivos cujo nome contenha "c6".
    Suporta extrato com preâmbulo (linhas informativas antes do cabeçalho).
    """

    parser_id = "c6_csv"
    institution = "C6 Bank"

    @classmethod
    def can_parse(cls, filename: str, raw_content: bytes) -> bool:
        """
        Retorna True se o arquivo é reconhecido como CSV do C6 Bank.

        Critérios (qualquer um é suficiente):
        1. Nome do arquivo contém "c6".
        2. As colunas do CSV correspondem ao padrão de fatura ou extrato C6.
           peek_headers() já ignora preâmbulos, encontrando o cabeçalho real.
        """
        if not filename.lower().endswith(".csv"):
            return False

        filename_match = bool(re.search(r"\bc6\b|c6bank|c6-bank", filename, re.IGNORECASE))

        try:
            headers = peek_headers(raw_content)
            norm = {normalize_col_name(h) for h in headers}

            def has(kw: str) -> bool:
                return any(kw in n for n in norm)

            header_match = (
                (has("entrada") and has("saida"))           # extrato novo
                or (has("historico") and has("saldo"))      # extrato formato antigo
                or (has("parcela") and has("cartao"))       # fatura novo
                or (has("data") and has("descricao") and has("valor") and not has("parcela"))
            )
        except Exception:
            header_match = False

        return filename_match or header_match

    def parse(self, filename: str, raw_content: bytes) -> list[RawTransaction]:
        """Extrai transações do CSV C6 Bank, detectando tipo automaticamente."""
        result = read_csv_bytes(raw_content, auto_detect_skip=True)

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
        """Retorna 'fatura', 'extrato' ou None."""
        norm = {normalize_col_name(h) for h in headers}

        def has(kw: str) -> bool:
            return any(kw in n for n in norm)

        # Extrato: tem colunas entrada/saída separadas ou histórico+saldo
        if has("entrada") and has("saida"):
            return "extrato"
        if has("historico") and has("saldo"):
            return "extrato"

        # Fatura: tem parcela, ou cartão, ou data+descricao+valor
        if has("parcela") or has("cartao"):
            return "fatura"
        if has("data") and has("descricao") and has("valor"):
            return "fatura"

        return None

    def _find_col(self, df: pd.DataFrame, norm_target: str) -> str | None:
        """Retorna o nome original da coluna cujo nome normalizado é exatamente `norm_target`."""
        for col in df.columns:
            if normalize_col_name(col) == norm_target:
                return col
        return None

    def _find_col_like(self, df: pd.DataFrame, keyword: str) -> str | None:
        """Retorna o nome original da primeira coluna cujo nome normalizado contém `keyword`."""
        for col in df.columns:
            if keyword in normalize_col_name(col):
                return col
        return None

    def _parse_fatura(self, df: pd.DataFrame, filename: str) -> list[RawTransaction]:
        """
        Parseia fatura do cartão C6 Bank.

        Suporta o formato novo (Data de Compra / Valor (em R$) / Parcela)
        e o formato legado (Data / Descrição / Valor).

        Convenção de valor:
        - Positivo → despesa → type="debit"
        - Negativo → estorno ou pagamento → type="credit"
        """
        # Data: "Data de Compra" (novo) ou "Data" (legado)
        col_data = self._find_col_like(df, "data") or self._find_col(df, "data")
        # Descrição
        col_desc = self._find_col(df, "descricao")
        # Valor em R$: prefere "valor (em r$)" (novo), fallback para "valor" (legado)
        col_valor = self._find_col_like(df, "valor (em r") or self._find_col_like(df, "valor em r")
        if col_valor is None:
            col_valor = self._find_col_like(df, "valor")
        # Parcela (opcional)
        col_parcela = self._find_col(df, "parcela")

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
                parcela_raw = str(row[col_parcela]).strip() if col_parcela else None

                if _is_empty(date_raw) or _is_empty(desc_raw) or _is_empty(valor_raw):
                    continue
                if date_raw.lower() == "data" or date_raw.lower() == "data de compra":
                    continue  # cabeçalho repetido

                # Normaliza parcela
                installment: str | None = None
                if parcela_raw and not _is_empty(parcela_raw):
                    installment = parcela_raw

                # Linha de pagamento da fatura (crédito do banco no cartão):
                # ignorada porque já aparece como débito no extrato bancário.
                if any(kw in desc_raw.lower() for kw in _FATURA_PAYMENT_KEYWORDS):
                    logger.debug("Linha de pagamento ignorada na fatura: '%s'", desc_raw)
                    continue

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
                    installment=installment,
                    raw_text=f"{date_raw}|{desc_raw}|{valor_raw}",
                ))

            except Exception as exc:
                logger.error(
                    "C6 fatura '%s', linha %s ignorada: %s",
                    filename, idx, exc, exc_info=True,
                )

        logger.info("C6 fatura '%s': %d transação(ões) extraída(s).", filename, len(transactions))
        return transactions

    def _parse_extrato(self, df: pd.DataFrame, filename: str) -> list[RawTransaction]:
        """
        Parseia extrato da conta corrente C6 Bank.

        Suporta o formato novo com colunas Entrada(R$)/Saída(R$) separadas
        e o formato legado com coluna Valor única.

        Formato novo — convenção:
        - Entrada(R$) > 0 → crédito (depósito, PIX recebido)
        - Saída(R$)   > 0 → débito  (pagamento, PIX enviado)

        Formato legado — convenção:
        - Valor positivo → crédito
        - Valor negativo → débito
        """
        # Data: primeira coluna que contenha "data" (ex: "Data Lançamento")
        col_data = self._find_col_like(df, "data") or self._find_col(df, "data")
        # Título/Histórico: descrição principal
        col_titulo = (
            self._find_col(df, "titulo")
            or self._find_col(df, "historico")
        )
        # Descrição complementar
        col_desc = self._find_col(df, "descricao")
        # Formato novo: entrada e saída separadas
        col_entrada = self._find_col_like(df, "entrada")
        col_saida = self._find_col_like(df, "saida")
        # Formato legado: valor único
        col_valor = self._find_col(df, "valor") if not (col_entrada and col_saida) else None

        use_entrada_saida = bool(col_entrada and col_saida)

        if not col_data or (not use_entrada_saida and not col_valor):
            logger.error(
                "C6 extrato '%s': colunas obrigatórias ausentes. "
                "Encontradas: %s",
                filename, list(df.columns),
            )
            return []

        transactions: list[RawTransaction] = []

        for idx, row in df.iterrows():
            try:
                date_raw = str(row[col_data]).strip()

                if _is_empty(date_raw) or date_raw.lower() in ("data", "data lancamento"):
                    continue

                titulo_raw = str(row[col_titulo]).strip() if col_titulo else ""
                desc_raw = str(row[col_desc]).strip() if col_desc else ""

                # Pula linhas de controle (ex: "SALDO ANTERIOR")
                if titulo_raw.lower() in _SKIP_HISTORICO:
                    logger.debug("Linha de controle ignorada: '%s'", titulo_raw)
                    continue

                # Monta descrição combinando título + descrição complementar
                if titulo_raw and desc_raw and titulo_raw.lower() != desc_raw.lower():
                    description = f"{titulo_raw} - {desc_raw}"
                elif titulo_raw:
                    description = titulo_raw
                elif desc_raw:
                    description = desc_raw
                else:
                    description = "(sem descrição)"

                date_str = parse_br_date(date_raw)

                if use_entrada_saida:
                    entrada_raw = str(row[col_entrada]).strip()
                    saida_raw = str(row[col_saida]).strip()

                    entrada = Decimal(0) if _is_empty(entrada_raw) else parse_br_amount(entrada_raw)
                    saida = Decimal(0) if _is_empty(saida_raw) else parse_br_amount(saida_raw)

                    if entrada > 0:
                        tx_type: Literal["debit", "credit"] = "credit"
                        amount_abs = entrada
                    elif saida > 0:
                        tx_type = "debit"
                        amount_abs = saida
                    else:
                        continue  # ambos zero — linha irrelevante
                else:
                    valor_raw = str(row[col_valor]).strip()
                    if _is_empty(valor_raw):
                        continue
                    amount_signed = parse_br_amount(valor_raw)
                    if amount_signed >= 0:
                        tx_type = "credit"
                        amount_abs = amount_signed
                    else:
                        tx_type = "debit"
                        amount_abs = -amount_signed

                is_transfer = any(kw in description.lower() for kw in _CC_PAYMENT_KEYWORDS)

                transactions.append(RawTransaction(
                    date=date_str,
                    description=description,
                    amount=amount_abs,
                    type=tx_type,
                    is_transfer=is_transfer,
                    raw_text=f"{date_raw}|{titulo_raw}|{desc_raw}",
                ))

            except Exception as exc:
                logger.error(
                    "C6 extrato '%s', linha %s ignorada: %s",
                    filename, idx, exc, exc_info=True,
                )

        logger.info("C6 extrato '%s': %d transação(ões) extraída(s).", filename, len(transactions))
        return transactions


# ─── Utilitários locais ───────────────────────────────────────────────────────

def _is_empty(value: str) -> bool:
    """Retorna True se o valor é vazio, NaN (string) ou traço."""
    return value.lower() in ("", "nan", "-", "n/a", "none")
