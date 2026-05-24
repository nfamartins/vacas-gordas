"""Utilitário de extração de tabelas e texto de arquivos PDF usando pdfplumber.

Não é um parser de banco — é chamado pelos parsers PDF (ex: NubankPdfParser)
para obter dados estruturados do arquivo.

Estratégia de extração de tabelas (três tentativas progressivas):
1. Configuração padrão do pdfplumber.
2. Detecção por linhas físicas ("lines").
3. Detecção por alinhamento de texto ("text") — para PDFs sem bordas visíveis.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

import pdfplumber

logger = logging.getLogger(__name__)

# Estratégias de extração, em ordem crescente de agressividade
_TABLE_STRATEGIES: list[dict] = [
    {},  # padrão do pdfplumber (detecção automática)
    {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
    {"vertical_strategy": "text", "horizontal_strategy": "text"},
]


# ─── Dataclass de resultado ───────────────────────────────────────────────────

@dataclass
class PdfTable:
    """Representa uma tabela extraída de uma página do PDF."""

    page_number: int
    """Número da página de origem (base 1)."""

    rows: list[list[str]]
    """
    Linhas da tabela. Cada célula é uma string (nunca None — None é convertido
    para string vazia na extração). rows[0] pode ser o cabeçalho.
    """

    strategy_used: str = "default"
    """Estratégia de extração que funcionou para esta tabela."""


# ─── Funções públicas ─────────────────────────────────────────────────────────

def extract_tables(raw_content: bytes) -> list[PdfTable]:
    """
    Extrai todas as tabelas de um PDF em bytes.

    Itera cada página e tenta as três estratégias de extração em ordem.
    Páginas com erro são logadas e ignoradas — o resto do documento é processado.

    Returns:
        Lista de PdfTable com todas as tabelas encontradas, em ordem de página.
    """
    tables: list[PdfTable] = []

    try:
        with pdfplumber.open(io.BytesIO(raw_content)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_tables = _extract_page_tables(page, page_num)
                tables.extend(page_tables)
    except Exception as exc:
        logger.error("Erro ao abrir PDF para extração de tabelas: %s", exc, exc_info=True)

    logger.debug("Tabelas extraídas do PDF: %d", len(tables))
    return tables


def extract_text_lines(raw_content: bytes) -> list[str]:
    """
    Extrai todas as linhas de texto do PDF como fallback (quando não há tabelas).

    Remove linhas em branco e faz strip em cada linha.

    Returns:
        Lista de strings não-vazias, uma por linha de texto.
    """
    lines: list[str] = []

    try:
        with pdfplumber.open(io.BytesIO(raw_content)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                    page_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                    lines.extend(page_lines)
                except Exception as exc:
                    logger.warning(
                        "Erro ao extrair texto da página %d: %s", page_num, exc
                    )
    except Exception as exc:
        logger.error("Erro ao abrir PDF para extração de texto: %s", exc, exc_info=True)

    logger.debug("Linhas de texto extraídas do PDF: %d", len(lines))
    return lines


def extract_first_page_text(raw_content: bytes, max_chars: int = 1_500) -> str:
    """
    Extrai o texto da primeira página do PDF.

    Usado principalmente por can_parse() para identificar a instituição financeira
    sem precisar processar o documento inteiro.

    Returns:
        String com até `max_chars` caracteres. String vazia em caso de erro.
    """
    try:
        with pdfplumber.open(io.BytesIO(raw_content)) as pdf:
            if not pdf.pages:
                return ""
            return (pdf.pages[0].extract_text() or "")[:max_chars]
    except Exception:
        return ""


def is_pdf(raw_content: bytes) -> bool:
    """Verifica se os bytes correspondem a um arquivo PDF pelo magic number (%PDF)."""
    return raw_content[:4] == b"%PDF"


# ─── Funções internas ─────────────────────────────────────────────────────────

def _extract_page_tables(page: pdfplumber.page.Page, page_num: int) -> list[PdfTable]:
    """
    Tenta extrair tabelas de uma página usando múltiplas estratégias.

    Retorna lista vazia se nenhuma estratégia encontrar tabelas ou todas falharem.
    """
    strategy_names = ["default", "lines", "text"]

    for strategy, strategy_name in zip(_TABLE_STRATEGIES, strategy_names):
        try:
            if strategy:
                raw_tables = page.extract_tables(table_settings=strategy)
            else:
                raw_tables = page.extract_tables()

            if not raw_tables:
                continue

            result: list[PdfTable] = []
            for raw_table in raw_tables:
                # Normaliza células: None → "", strip em strings
                rows = [
                    [_clean_cell(cell) for cell in row]
                    for row in raw_table
                    if any(cell for cell in row if cell)  # filtra linhas vazias
                ]
                if rows:
                    result.append(PdfTable(
                        page_number=page_num,
                        rows=rows,
                        strategy_used=strategy_name,
                    ))

            if result:
                logger.debug(
                    "Página %d: %d tabela(s) extraída(s) com estratégia '%s'",
                    page_num, len(result), strategy_name,
                )
                return result

        except Exception as exc:
            logger.debug(
                "Estratégia '%s' falhou na página %d: %s",
                strategy_name, page_num, exc,
            )
            continue

    return []


def _clean_cell(cell) -> str:
    """Converte célula de tabela para string limpa."""
    if cell is None:
        return ""
    return str(cell).strip()
