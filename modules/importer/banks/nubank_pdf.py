"""Parser para faturas do Nubank em formato PDF.

Estratégia de extração (ordem de tentativa):
1. Extração de tabelas via pdfplumber (3 estratégias progressivas).
2. Fallback: extração de texto + parsing por regex.

Particularidades tratadas:
- Datas no formato "DD MMM" sem ano → inferido do nome do arquivo ou do cabeçalho.
- Descrições multi-linha → acumuladas quando data/valor ausentes na linha.
- Linhas de info de moeda estrangeira (ex: "123.45 USD") → ignoradas.
- Internacional: linha BRL + linha informativa com valor em moeda original.
"""
from __future__ import annotations

import logging
import re
from datetime import date as date_type
from decimal import Decimal
from typing import Literal

from modules.importer.base_parser import (
    BaseParser,
    RawTransaction,
    parse_br_amount,
    parse_br_date,
)
from modules.importer.pdf_reader import (
    PdfTable,
    extract_first_page_text,
    extract_tables,
    extract_text_lines,
    is_pdf,
)

logger = logging.getLogger(__name__)

# Regex para linha de info de moeda estrangeira: "123.45 USD" ou "R$ 45,90 (1.23 USD)"
_RE_FOREIGN_CURRENCY = re.compile(
    r"^\d[\d.,]*\s+[A-Z]{3}$|^\([\d.,]+\s+[A-Z]{3}\)$", re.IGNORECASE
)

# Regex para linha de transação no texto do PDF:
# "15 JAN   IFOOD*RESTAURANTE   R$ 45,90" ou "15 jan   descrição   45,90"
_RE_TX_LINE = re.compile(
    r"^(\d{1,2}\s+[A-Za-z]{3}(?:\s+\d{4})?)"  # data (grupo 1)
    r"\s{2,}"                                    # separação
    r"(.+?)"                                     # descrição (grupo 2, não-greedy)
    r"\s{2,}"                                    # separação
    r"(R\$\s*[\d.,]+|-?[\d.,]+)$",              # valor (grupo 3)
    re.IGNORECASE,
)

# Palavras-chave que identificam a tabela de transações Nubank
_HEADER_KEYWORDS = frozenset({"data", "descricao", "valor", "lancamento", "compra"})


class NubankPdfParser(BaseParser):
    """
    Parser para faturas do Nubank em formato PDF.

    Detecta o arquivo tanto pelo nome quanto pela presença de "nubank" ou
    "Nu Pagamentos" no texto da primeira página.
    """

    parser_id = "nubank_pdf"
    institution = "Nubank"

    @classmethod
    def can_parse(cls, filename: str, raw_content: bytes) -> bool:
        """
        Retorna True se o arquivo é reconhecido como fatura PDF do Nubank.

        Critérios (qualquer um é suficiente):
        1. Arquivo é um PDF (magic bytes %PDF).
        2. Nome do arquivo contém "nubank".
        3. Texto da primeira página contém "nubank" ou "Nu Pagamentos".
        """
        if not is_pdf(raw_content):
            return False

        filename_match = bool(re.search(r"nubank", filename, re.IGNORECASE))
        if filename_match:
            return True

        # Verifica conteúdo sem processar o PDF inteiro
        first_page_text = extract_first_page_text(raw_content, max_chars=1_500)
        content_match = bool(
            re.search(r"nubank|nu pagamentos|nu\.com\.br", first_page_text, re.IGNORECASE)
        )
        return content_match

    def parse(self, filename: str, raw_content: bytes) -> list[RawTransaction]:
        """Extrai transações da fatura PDF Nubank."""
        # Infere o ano de referência a partir do nome do arquivo
        ref_year = _infer_year_from_filename(filename) or date_type.today().year

        # Tenta extração por tabelas primeiro
        tables = extract_tables(raw_content)
        tx_table = self._find_transaction_table(tables)

        if tx_table is not None:
            logger.debug(
                "Fatura Nubank '%s': usando extração por tabela (página %d, estratégia '%s').",
                filename, tx_table.page_number, tx_table.strategy_used,
            )
            transactions = self._parse_table(tx_table, filename, ref_year)
        else:
            logger.debug(
                "Fatura Nubank '%s': tabelas não encontradas, usando extração de texto.",
                filename,
            )
            lines = extract_text_lines(raw_content)
            # Tenta inferir ano mais preciso a partir do texto do PDF
            ref_year = _infer_year_from_text(lines) or ref_year
            transactions = self._parse_text_lines(lines, filename, ref_year)

        logger.info(
            "Fatura Nubank PDF '%s': %d transação(ões) extraída(s).",
            filename, len(transactions),
        )
        return transactions

    # ── Extração por tabela ───────────────────────────────────────────────────

    def _find_transaction_table(self, tables: list[PdfTable]) -> PdfTable | None:
        """
        Identifica a tabela que contém os lançamentos da fatura.

        Procura por uma tabela cuja primeira linha (cabeçalho) contenha
        palavras-chave relacionadas a data, descrição ou valor.
        """
        from modules.importer.base_parser import normalize_col_name

        for table in tables:
            if not table.rows:
                continue
            header_row = table.rows[0]
            norm_headers = {normalize_col_name(cell) for cell in header_row if cell}
            if norm_headers & _HEADER_KEYWORDS:
                return table
        return None

    def _parse_table(
        self, table: PdfTable, filename: str, ref_year: int
    ) -> list[RawTransaction]:
        """
        Parseia a tabela de transações da fatura Nubank.

        Detecta automaticamente os índices das colunas de data, descrição e valor
        a partir do cabeçalho da tabela.
        """
        from modules.importer.base_parser import normalize_col_name

        if not table.rows:
            return []

        # Detecta índices das colunas pelo cabeçalho
        header = table.rows[0]
        idx_date = idx_desc = idx_amount = None

        for i, cell in enumerate(header):
            norm = normalize_col_name(cell)
            if "data" in norm or "lancamento" in norm or "compra" in norm:
                idx_date = i
            elif "descricao" in norm or "descri" in norm or "estabelecimento" in norm:
                idx_desc = i
            elif "valor" in norm or "total" in norm:
                idx_amount = i

        # Fallback: assume posições padrão Nubank (data=0, desc=1, valor=-1)
        if idx_date is None:
            idx_date = 0
        if idx_desc is None:
            idx_desc = 1
        if idx_amount is None:
            idx_amount = len(header) - 1

        transactions: list[RawTransaction] = []
        # Acumulador para descrições multi-linha
        pending_date: str | None = None
        pending_desc_parts: list[str] = []

        for row_idx, row in enumerate(table.rows[1:], start=1):
            try:
                if len(row) <= max(idx_date, idx_desc, idx_amount):
                    # Linha com menos colunas: parte de multi-linha
                    if pending_desc_parts:
                        extra = " ".join(c for c in row if c)
                        if extra:
                            pending_desc_parts.append(extra)
                    continue

                date_cell = row[idx_date].strip()
                desc_cell = row[idx_desc].strip()
                amount_cell = row[idx_amount].strip()

                # Linha de info de moeda estrangeira → ignorar
                if _RE_FOREIGN_CURRENCY.match(desc_cell) or _RE_FOREIGN_CURRENCY.match(date_cell):
                    logger.debug("Linha de moeda estrangeira ignorada: %s", row)
                    continue

                has_date = bool(date_cell) and not _is_empty(date_cell)
                has_amount = bool(amount_cell) and not _is_empty(amount_cell)

                # Linha de continuação de descrição (sem data nem valor)
                if not has_date and not has_amount:
                    if desc_cell and pending_desc_parts:
                        pending_desc_parts.append(desc_cell)
                    continue

                # Commita transação pendente antes de iniciar nova
                if pending_date and pending_desc_parts:
                    tx = _build_raw_tx(
                        date_str=pending_date,
                        description=" ".join(pending_desc_parts),
                        amount_str=None,  # não temos valor ainda — será na próxima iteração
                        ref_year=ref_year,
                        raw_text=str(row),
                    )
                    # Só adiciona se tiver valor — caso raro de multi-linha com valor separado
                    # Na prática, Nubank sempre tem data+valor na mesma linha

                # Inicia nova transação
                if has_date:
                    pending_date = _normalize_pdf_date(date_cell, ref_year)
                    pending_desc_parts = [desc_cell] if desc_cell else []

                if has_amount and pending_date:
                    full_desc = " ".join(pending_desc_parts) if pending_desc_parts else desc_cell
                    if not full_desc:
                        full_desc = "(sem descrição)"

                    tx = _build_raw_tx(
                        date_str=pending_date,
                        description=full_desc,
                        amount_str=amount_cell,
                        ref_year=ref_year,
                        raw_text="|".join(str(c) for c in row),
                    )
                    if tx:
                        transactions.append(tx)

                    # Reseta acumulador
                    pending_date = None
                    pending_desc_parts = []

            except Exception as exc:
                logger.error(
                    "Nubank PDF '%s', linha de tabela %d ignorada: %s",
                    filename, row_idx, exc, exc_info=True,
                )

        return transactions

    # ── Extração por texto (fallback) ─────────────────────────────────────────

    def _parse_text_lines(
        self, lines: list[str], filename: str, ref_year: int
    ) -> list[RawTransaction]:
        """
        Parseia linhas de texto quando a extração de tabelas falha.

        Usa regex para detectar linhas no formato:
        "DD MMM   DESCRIÇÃO   VALOR"
        """
        transactions: list[RawTransaction] = []

        for line_idx, line in enumerate(lines):
            try:
                # Linha de info de moeda estrangeira
                if _RE_FOREIGN_CURRENCY.match(line.strip()):
                    continue

                m = _RE_TX_LINE.match(line.strip())
                if not m:
                    continue

                date_raw, desc_raw, amount_raw = m.groups()

                tx = _build_raw_tx(
                    date_str=_normalize_pdf_date(date_raw.strip(), ref_year),
                    description=desc_raw.strip(),
                    amount_str=amount_raw.strip(),
                    ref_year=ref_year,
                    raw_text=line,
                )
                if tx:
                    transactions.append(tx)

            except Exception as exc:
                logger.error(
                    "Nubank PDF '%s', linha de texto %d ignorada: %s",
                    filename, line_idx, exc, exc_info=True,
                )

        return transactions


# ─── Funções auxiliares ────────────────────────────────────────────────────────

def _normalize_pdf_date(date_raw: str, ref_year: int) -> str:
    """
    Normaliza data do PDF Nubank para YYYY-MM-DD.

    O formato Nubank no PDF é "DD MMM" (ex: "15 JAN") sem o ano.
    O ano é inferido do contexto (nome do arquivo ou cabeçalho da fatura).
    """
    # Se já tem 4 dígitos de ano, usa parse_br_date diretamente
    if re.search(r"\d{4}", date_raw):
        return parse_br_date(date_raw)

    # Adiciona o ano de referência
    return parse_br_date(f"{date_raw} {ref_year}")


def _infer_year_from_filename(filename: str) -> int | None:
    """Extrai o ano do nome do arquivo (ex: 'fatura_2024_05.pdf' → 2024)."""
    m = re.search(r"\b(20\d{2})\b", filename)
    return int(m.group(1)) if m else None


def _infer_year_from_text(lines: list[str]) -> int | None:
    """
    Tenta encontrar o ano de referência no texto extraído do PDF.

    Procura por padrões como "FATURA DE MAIO/2024" ou "Vencimento: 10/06/2024".
    """
    for line in lines[:30]:  # cabeçalho geralmente está nas primeiras linhas
        m = re.search(r"\b(20\d{2})\b", line)
        if m:
            return int(m.group(1))
    return None


def _build_raw_tx(
    date_str: str,
    description: str,
    amount_str: str | None,
    ref_year: int,
    raw_text: str,
) -> RawTransaction | None:
    """
    Constrói RawTransaction a partir dos campos brutos extraídos.

    Retorna None se os dados forem inválidos (ex: valor não parseável).

    Nubank fatura: valor positivo = despesa do usuário = debit.
                   valor negativo = pagamento/estorno = credit.
    """
    if amount_str is None or _is_empty(amount_str):
        return None

    try:
        amount_signed = parse_br_amount(amount_str)
    except ValueError:
        logger.debug("Valor não parseável ignorado: '%s'", amount_str)
        return None

    if amount_signed >= 0:
        tx_type: Literal["debit", "credit"] = "debit"
        amount_abs = amount_signed
    else:
        tx_type = "credit"
        amount_abs = -amount_signed

    if not description:
        description = "(sem descrição)"

    return RawTransaction(
        date=date_str,
        description=description,
        amount=amount_abs,
        type=tx_type,
        raw_text=raw_text,
    )


def _is_empty(value: str) -> bool:
    """Retorna True se o valor é vazio, NaN ou traço."""
    return value.lower().strip() in ("", "nan", "-", "n/a", "none", "r$")
