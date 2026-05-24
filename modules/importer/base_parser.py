"""Módulo base para todos os parsers de arquivos financeiros.

Define:
- RawTransaction: modelo Pydantic v2 que representa uma transação extraída
  antes de qualquer normalização para o banco de dados.
- BaseParser: classe abstrata que todos os parsers de bancos devem implementar.
- Utilitários compartilhados: parse_br_date, parse_br_amount.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from abc import ABC, abstractmethod
from datetime import date as date_type
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

# ─── Tabela de meses (pt-BR e en) para parse de datas textuais ───────────────
_MONTH_ABBREV: dict[str, int] = {
    # Português
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    # Inglês (Nubank pode usar nas PDFs)
    "feb": 2, "apr": 4, "may": 5, "aug": 8, "sep": 9, "oct": 10,
    "dec": 12,
}


# ─── Modelo de transação bruta ────────────────────────────────────────────────

class RawTransaction(BaseModel):
    """
    Representa uma transação bruta extraída de um arquivo, antes da normalização
    para o schema MongoDB.

    Invariante de design:
    - `amount` é SEMPRE ≥ 0 (valor absoluto).
    - `type` carrega a direção: "debit" = saída de dinheiro, "credit" = entrada.

    Isso evita ambiguidades entre bancos que usam sinal positivo/negativo
    de formas diferentes em seus arquivos exportados.
    """

    date: str
    """Data da transação no formato YYYY-MM-DD."""

    description: str
    """Descrição original da transação, conforme consta no arquivo-fonte."""

    amount: Decimal
    """Valor absoluto da transação (sempre ≥ 0)."""

    type: Literal["debit", "credit"]
    """Direção: débito (saída) ou crédito (entrada)."""

    currency: str = "BRL"
    """Código da moeda ISO 4217 (padrão: BRL)."""

    raw_text: str = ""
    """Linha/bloco original do arquivo-fonte, para auditoria e depuração."""

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """Garante que a data está no formato YYYY-MM-DD."""
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError(
                f"Data inválida: '{v}'. Formato esperado: YYYY-MM-DD."
            )
        return v

    @field_validator("amount")
    @classmethod
    def validate_positive_amount(cls, v: Decimal) -> Decimal:
        """Garante que o valor é não-negativo."""
        if v < 0:
            raise ValueError(
                f"'amount' deve ser ≥ 0, recebido: {v}. "
                "Use o campo 'type' para indicar a direção."
            )
        return v

    @field_validator("description")
    @classmethod
    def validate_non_empty_description(cls, v: str) -> str:
        """Garante que a descrição não está vazia após strip."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("'description' não pode ser vazia.")
        return stripped


# ─── Classe base abstrata ─────────────────────────────────────────────────────

class BaseParser(ABC):
    """
    Classe base abstrata para todos os parsers de arquivos financeiros.

    Cada banco deve criar uma subclasse em modules/importer/banks/ e implementar:
    - can_parse(): detecta automaticamente se o arquivo pertence a este parser.
    - parse(): extrai a lista de RawTransactions do arquivo.

    Atributos de classe obrigatórios (definir na subclasse):
    - parser_id: str  — identificador único, ex: "c6_csv"
    - institution: str — nome da instituição, ex: "C6 Bank"
    """

    parser_id: str
    institution: str

    @classmethod
    @abstractmethod
    def can_parse(cls, filename: str, raw_content: bytes) -> bool:
        """
        Retorna True se este parser reconhece o arquivo fornecido.

        A detecção deve usar:
        1. Nome/extensão do arquivo (mais rápido, menos confiável).
        2. Conteúdo do arquivo — cabeçalho CSV, magic bytes, texto interno.

        NUNCA deve lançar exceção: retornar False em qualquer erro.
        """
        ...

    @abstractmethod
    def parse(self, filename: str, raw_content: bytes) -> list[RawTransaction]:
        """
        Extrai e retorna a lista de transações brutas do arquivo.

        Regras obrigatórias para implementações:
        - Capturar exceções individualmente por linha/bloco e continuar.
        - Logar erros com logger.error() incluindo filename e índice da linha.
        - Nunca retornar None — lista vazia é o resultado correto para arquivo inválido.
        """
        ...


# ─── Utilitários compartilhados por todos os parsers ─────────────────────────

def parse_br_date(value: str) -> str:
    """
    Converte datas em formatos brasileiros/comuns para YYYY-MM-DD.

    Formatos suportados:
    - DD/MM/YYYY  → "20/05/2024"
    - DD/MM/YY    → "20/05/24"  (assume século 2000)
    - YYYY-MM-DD  → já no formato correto, retorna sem alteração
    - DD MMM YYYY → "20 Jan 2024" (Nubank PDF, inglês/português)
    - DD MMM      → "20 Jan"      (Nubank PDF sem ano → usa ano corrente)

    Raises:
        ValueError: se o formato não for reconhecido.
    """
    value = value.strip()

    # Já no formato correto
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value

    # DD/MM/YYYY ou DD/MM/YY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", value)
    if m:
        day, month, year = m.groups()
        year_int = int(year)
        if year_int < 100:
            year_int += 2000
        return f"{year_int:04d}-{int(month):02d}-{int(day):02d}"

    # DD MMM YYYY ou DD MMM (ex: "20 JAN 2024", "20 jan", "20 Janeiro 2024")
    m = re.match(
        r"^(\d{1,2})\s+([A-Za-zÀ-ɏ]+)(?:\s+(\d{4}))?$",
        value,
    )
    if m:
        day_str, month_str, year_str = m.groups()
        month_key = month_str.lower()[:3]
        month_num = _MONTH_ABBREV.get(month_key)
        if month_num:
            year_int = int(year_str) if year_str else date_type.today().year
            return f"{year_int:04d}-{month_num:02d}-{int(day_str):02d}"

    raise ValueError(f"Formato de data não reconhecido: '{value}'")


def parse_br_amount(value: str) -> Decimal:
    """
    Converte string de valor monetário brasileiro para Decimal (com sinal).

    Suporta:
    - "1.234,56"   → Decimal("1234.56")
    - "1234,56"    → Decimal("1234.56")
    - "-45,90"     → Decimal("-45.90")
    - "R$ 45,90"   → Decimal("45.90")
    - "1234.56"    → Decimal("1234.56")  (formato US, aceito como fallback)

    Raises:
        ValueError: se o valor não puder ser convertido.
    """
    # Remove símbolo de moeda, espaços e aspas
    clean = re.sub(r"[R$\s \"']", "", value).strip()

    if not clean:
        raise ValueError(f"Valor vazio após limpeza: '{value}'")

    # Preserva o sinal
    negative = clean.startswith("-")
    clean = clean.lstrip("+-")

    # Formato BR: ponto como milhar E vírgula como decimal → "1.234,56"
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    # Só vírgula → separador decimal BR → "1234,56" ou "45,90"
    elif "," in clean:
        clean = clean.replace(",", ".")
    # Só ponto → separador decimal US → "1234.56" (já ok)

    try:
        result = Decimal(clean)
    except InvalidOperation as exc:
        raise ValueError(f"Valor monetário inválido: '{value}'") from exc

    return -result if negative else result


def normalize_col_name(col: str) -> str:
    """
    Normaliza nome de coluna para comparação case-insensitive sem acentos.

    Exemplo: "Descrição" → "descricao"
    """
    nfkd = unicodedata.normalize("NFD", col.lower().strip())
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
