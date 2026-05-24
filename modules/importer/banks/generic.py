"""Parser genérico de CSV para arquivos de bancos não suportados nativamente.

Diferente dos outros parsers, o GenericCsvParser opera em dois modos:

1. **Preview** (`preview()`):
   Lê o arquivo e retorna informações sobre colunas e uma amostra de dados.
   Chamado pela UI Streamlit para exibir ao usuário e solicitar o mapeamento.

2. **Parse** (`parse(..., column_mapping=...)`):
   Executa a extração usando o mapeamento fornecido pelo usuário via UI.
   Lança ValueError se o mapeamento não for fornecido.

O parser NUNCA é selecionado automaticamente pelo detect_parser() — seu
can_parse() sempre retorna False. Deve ser instanciado explicitamente quando
nenhum parser específico reconhece o arquivo.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from modules.importer.base_parser import BaseParser, RawTransaction, parse_br_amount
from modules.importer.csv_reader import read_csv_bytes

logger = logging.getLogger(__name__)


# ─── Modelos de dados ─────────────────────────────────────────────────────────

class ColumnMapping(BaseModel):
    """
    Define o mapeamento de colunas do arquivo CSV para os campos da transação.

    Todos os campos de nome de coluna devem corresponder exatamente ao nome
    da coluna no DataFrame retornado por preview().
    """

    date_column: str
    """Nome da coluna que contém a data da transação."""

    description_column: str
    """Nome da coluna que contém a descrição/histórico da transação."""

    amount_column: str
    """Nome da coluna que contém o valor da transação."""

    date_format: str = "%d/%m/%Y"
    """
    Formato strptime para parse de datas.
    Exemplos:
    - "%d/%m/%Y"  → "20/05/2024"
    - "%Y-%m-%d"  → "2024-05-20"
    - "%d/%m/%y"  → "20/05/24"
    """

    decimal_separator: str = ","
    """Separador decimal no arquivo (vírgula para BR, ponto para US)."""

    thousands_separator: str = "."
    """Separador de milhar no arquivo (ponto para BR, vírgula para US)."""

    amount_sign_column: str | None = None
    """
    Nome da coluna opcional com indicador de débito/crédito.
    Ex: coluna "Tipo" com valores "D" e "C".
    Se None, o sinal do valor em amount_column determina a direção.
    """

    credit_indicator: str | None = None
    """
    Valor na coluna amount_sign_column que indica crédito (entrada de dinheiro).
    Ex: "C", "CREDITO", "+".
    Quando amount_sign_column está definido e o valor NÃO corresponde a este
    indicador, a transação é tratada como débito.
    """

    skip_rows: int = 0
    """Número de linhas a pular antes do cabeçalho."""

    encoding: str | None = None
    """Encoding do arquivo (auto-detectado se None)."""


class PreviewResult(BaseModel):
    """
    Resultado do modo preview, retornado para a UI Streamlit.

    Contém as informações necessárias para o usuário configurar o mapeamento.
    """

    columns: list[str]
    """Nomes das colunas detectadas no arquivo."""

    sample_rows: list[dict[str, Any]]
    """Primeiras 5 linhas de dados como lista de dicionários."""

    detected_encoding: str
    """Encoding detectado (informativo para o usuário)."""

    detected_delimiter: str
    """Delimitador detectado (informativo para o usuário)."""

    total_rows: int
    """Total de linhas de dados no arquivo (sem contar o cabeçalho)."""


# ─── Parser ───────────────────────────────────────────────────────────────────

class GenericCsvParser(BaseParser):
    """
    Parser genérico de CSV para arquivos de instituições não suportadas.

    Fluxo de uso na UI Streamlit:
    1. Usuário faz upload do arquivo.
    2. detect_parser() retorna None (nenhum parser específico reconheceu).
    3. UI instancia GenericCsvParser() e chama preview().
    4. UI exibe colunas e amostra, solicita mapeamento ao usuário.
    5. Usuário define o mapeamento via formulário Streamlit.
    6. UI chama parse(..., column_mapping=mapping_definido).
    """

    parser_id = "generic_csv"
    institution = "Genérico"

    @classmethod
    def can_parse(cls, filename: str, raw_content: bytes) -> bool:
        """
        Sempre retorna False.

        O parser genérico nunca é selecionado automaticamente — deve ser
        instanciado explicitamente pela UI quando nenhum parser específico
        reconhece o arquivo.
        """
        return False

    def preview(self, filename: str, raw_content: bytes) -> PreviewResult:
        """
        Modo preview: lê o CSV e retorna metadados para a UI Streamlit.

        Não faz nenhuma interpretação de datas ou valores — retorna tudo
        como string para que o usuário possa inspecionar antes de mapear.

        Args:
            filename:    Nome do arquivo (usado apenas para log).
            raw_content: Conteúdo binário do arquivo CSV.

        Returns:
            PreviewResult com colunas, amostra e informações do arquivo.
        """
        result = read_csv_bytes(raw_content)

        if result.dataframe.empty:
            logger.warning("GenericCsvParser preview: arquivo '%s' vazio.", filename)
            return PreviewResult(
                columns=[],
                sample_rows=[],
                detected_encoding=result.detected_encoding,
                detected_delimiter=result.detected_delimiter,
                total_rows=0,
            )

        df = result.dataframe
        sample = df.head(5).to_dict(orient="records")

        return PreviewResult(
            columns=list(df.columns),
            sample_rows=sample,
            detected_encoding=result.detected_encoding,
            detected_delimiter=result.detected_delimiter,
            total_rows=len(df),
        )

    def parse(
        self,
        filename: str,
        raw_content: bytes,
        column_mapping: ColumnMapping | None = None,
    ) -> list[RawTransaction]:
        """
        Modo parse: extrai transações usando o mapeamento fornecido.

        Args:
            filename:       Nome do arquivo (para log e raw_text).
            raw_content:    Conteúdo binário do arquivo CSV.
            column_mapping: Mapeamento de colunas definido pelo usuário.
                            Obrigatório — lança ValueError se None.

        Returns:
            Lista de RawTransaction extraídas.

        Raises:
            ValueError: se column_mapping for None.
        """
        if column_mapping is None:
            raise ValueError(
                "column_mapping é obrigatório para o GenericCsvParser. "
                "Chame preview() primeiro para obter as colunas disponíveis, "
                "depois defina o mapeamento e chame parse() novamente."
            )

        result = read_csv_bytes(
            raw_content,
            encoding=column_mapping.encoding,
            skip_rows=column_mapping.skip_rows,
        )

        if result.dataframe.empty:
            logger.warning("GenericCsvParser: arquivo '%s' vazio.", filename)
            return []

        df = result.dataframe

        # Valida que as colunas mapeadas existem no DataFrame
        required_cols = [
            column_mapping.date_column,
            column_mapping.description_column,
            column_mapping.amount_column,
        ]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(
                f"Coluna(s) mapeada(s) não encontrada(s) no arquivo: {missing}. "
                f"Colunas disponíveis: {list(df.columns)}"
            )

        transactions: list[RawTransaction] = []

        for idx, row in df.iterrows():
            try:
                date_raw = str(row[column_mapping.date_column]).strip()
                desc_raw = str(row[column_mapping.description_column]).strip()
                amount_raw = str(row[column_mapping.amount_column]).strip()

                if _is_empty(date_raw) or _is_empty(desc_raw) or _is_empty(amount_raw):
                    continue

                # Parse de data
                date_str = self._convert_date(date_raw, column_mapping.date_format)

                # Parse de valor
                amount_abs, tx_type = self._convert_amount(
                    value=amount_raw,
                    mapping=column_mapping,
                    sign_value=(
                        str(row[column_mapping.amount_sign_column]).strip()
                        if column_mapping.amount_sign_column
                        and column_mapping.amount_sign_column in df.columns
                        else None
                    ),
                )

                transactions.append(RawTransaction(
                    date=date_str,
                    description=desc_raw,
                    amount=amount_abs,
                    type=tx_type,
                    raw_text=f"{date_raw}|{desc_raw}|{amount_raw}",
                ))

            except Exception as exc:
                logger.error(
                    "GenericCsvParser '%s', linha %s ignorada: %s",
                    filename, idx, exc, exc_info=True,
                )

        logger.info(
            "GenericCsvParser '%s': %d transação(ões) extraída(s).",
            filename, len(transactions),
        )
        return transactions

    # ── Métodos internos ──────────────────────────────────────────────────────

    def _convert_date(self, value: str, fmt: str) -> str:
        """
        Converte string de data usando o formato strptime do mapeamento.

        Retorna YYYY-MM-DD.

        Raises:
            ValueError: se o formato não corresponder.
        """
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                f"Data '{value}' não corresponde ao formato '{fmt}': {exc}"
            ) from exc

    def _convert_amount(
        self,
        value: str,
        mapping: ColumnMapping,
        sign_value: str | None,
    ) -> tuple[Decimal, Literal["debit", "credit"]]:
        """
        Converte string de valor para (Decimal positivo, "debit"|"credit").

        Lógica de determinação do tipo:
        1. Se amount_sign_column está definido:
           - Compara sign_value com credit_indicator (case-insensitive).
           - Se igual → credit; caso contrário → debit.
        2. Se não há sign_column:
           - Sinal do valor determina: positivo → crédito, negativo → débito.
           (Comum em extratos onde créditos são positivos e débitos negativos.)
        """
        # Normaliza separadores conforme o mapeamento
        clean = value.strip()
        if mapping.thousands_separator and mapping.thousands_separator != mapping.decimal_separator:
            clean = clean.replace(mapping.thousands_separator, "")
        if mapping.decimal_separator != ".":
            clean = clean.replace(mapping.decimal_separator, ".")

        # Preserva sinal
        negative = clean.startswith("-")
        clean_abs = clean.lstrip("+-")

        try:
            amount = Decimal(clean_abs)
        except Exception as exc:
            raise ValueError(f"Valor inválido após normalização: '{value}' → '{clean}'") from exc

        # Determina tipo pelo sign_column (se configurado)
        if mapping.amount_sign_column and sign_value is not None:
            credit_ind = (mapping.credit_indicator or "C").strip().lower()
            is_credit = sign_value.strip().lower() == credit_ind
            tx_type: Literal["debit", "credit"] = "credit" if is_credit else "debit"
            return amount, tx_type

        # Determina tipo pelo sinal do valor
        if negative:
            return amount, "debit"
        else:
            return amount, "credit"


# ─── Utilitários locais ───────────────────────────────────────────────────────

def _is_empty(value: str) -> bool:
    """Retorna True se o valor é vazio, NaN ou traço."""
    return value.lower() in ("", "nan", "-", "n/a", "none")
