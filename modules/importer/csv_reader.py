"""Utilitário de leitura de arquivos CSV com detecção automática de encoding e delimitador.

Nunca lança exceção para o chamador — todos os erros são logados e um resultado
seguro (DataFrame vazio) é retornado quando a leitura falha completamente.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass

import chardet
import pandas as pd

logger = logging.getLogger(__name__)

# Encodings testados em ordem de prioridade quando chardet não é conclusivo
_ENCODING_FALLBACKS = ["utf-8-sig", "cp1252", "latin-1"]

# Delimitadores candidatos para detecção por contagem
_DELIMITER_CANDIDATES = [",", ";", "\t", "|"]


# ─── Dataclasses de resultado ─────────────────────────────────────────────────

@dataclass
class CsvReadResult:
    """Resultado completo da leitura de um arquivo CSV."""

    dataframe: pd.DataFrame
    """DataFrame com os dados do CSV (pode estar vazio em caso de erro)."""

    detected_encoding: str
    """Encoding usado para decodificar o arquivo."""

    detected_delimiter: str
    """Delimitador de campos detectado."""

    header_row: list[str]
    """Nomes das colunas conforme lidos do arquivo."""


# ─── Funções públicas ─────────────────────────────────────────────────────────

def detect_encoding(raw_content: bytes) -> str:
    """
    Detecta o encoding de bytes usando chardet.

    Estratégia em ordem:
    1. chardet nos primeiros 10 KB (rápido e preciso para amostras grandes).
    2. Se confiança < 0.7, testa utf-8-sig (BOM do Excel).
    3. Fallback: cp1252 (Windows Western European — padrão em bancos BR).
    4. Último recurso: latin-1 (mapeia todos os 256 bytes, nunca falha).
    """
    sample = raw_content[:10_000]
    result = chardet.detect(sample)

    if result and result.get("confidence", 0) >= 0.7:
        encoding = result.get("encoding") or "utf-8-sig"
        logger.debug(
            "Encoding detectado por chardet: %s (confiança %.0f%%)",
            encoding, result["confidence"] * 100,
        )
        return encoding

    # Testa candidatos manualmente
    for enc in _ENCODING_FALLBACKS:
        try:
            raw_content.decode(enc)
            logger.debug("Encoding detectado por tentativa: %s", enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue

    logger.warning("Encoding não detectado; usando latin-1 como último recurso.")
    return "latin-1"


def detect_delimiter(sample: str) -> str:
    """
    Detecta o delimitador de campos CSV.

    Estratégia:
    1. csv.Sniffer nos primeiros 2048 caracteres.
    2. Contagem de ocorrências na primeira linha (escolhe o mais frequente).
    3. Padrão: vírgula.
    """
    # Tenta Sniffer
    try:
        dialect = csv.Sniffer().sniff(sample[:2048], delimiters=",;\t|")
        logger.debug("Delimitador detectado por Sniffer: %r", dialect.delimiter)
        return dialect.delimiter
    except csv.Error:
        pass

    # Conta ocorrências na primeira linha com dados
    lines = [ln for ln in sample.splitlines() if ln.strip()]
    first_line = lines[0] if lines else sample[:200]

    counts = {d: first_line.count(d) for d in _DELIMITER_CANDIDATES}
    best = max(counts, key=lambda d: counts[d])

    if counts[best] > 0:
        logger.debug("Delimitador detectado por contagem: %r", best)
        return best

    logger.warning("Delimitador não detectado; usando ',' como padrão.")
    return ","


def peek_headers(raw_content: bytes) -> list[str]:
    """
    Retorna apenas os nomes das colunas sem carregar todo o arquivo.

    Usado por can_parse() para inspecionar o CSV com custo mínimo.
    Retorna lista vazia em qualquer erro — nunca lança exceção.
    """
    try:
        encoding = detect_encoding(raw_content)
        text = raw_content.decode(encoding, errors="replace")
        delimiter = detect_delimiter(text)

        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        for row in reader:
            # Ignora linhas totalmente vazias (ex: linhas de cabeçalho do banco)
            if any(cell.strip() for cell in row):
                return [cell.strip() for cell in row]
        return []
    except Exception as exc:
        logger.debug("peek_headers falhou para o arquivo: %s", exc)
        return []


def read_csv_bytes(
    raw_content: bytes,
    encoding: str | None = None,
    delimiter: str | None = None,
    skip_rows: int = 0,
) -> CsvReadResult:
    """
    Lê bytes de CSV e retorna CsvReadResult com detecção automática de encoding
    e delimitador quando não fornecidos explicitamente.

    Nunca lança exceção — retorna DataFrame vazio com erro logado em caso de falha.

    Args:
        raw_content: Conteúdo binário do arquivo CSV.
        encoding:    Encoding a usar (auto-detectado se None).
        delimiter:   Delimitador a usar (auto-detectado se None).
        skip_rows:   Número de linhas a pular antes do cabeçalho (ex: linhas
                     informativas que alguns bancos incluem no topo do arquivo).

    Returns:
        CsvReadResult com DataFrame, encoding, delimitador e lista de colunas.
    """
    _empty = CsvReadResult(
        dataframe=pd.DataFrame(),
        detected_encoding=encoding or "utf-8",
        detected_delimiter=delimiter or ",",
        header_row=[],
    )

    if not raw_content:
        logger.warning("Conteúdo CSV vazio recebido.")
        return _empty

    # ── Detecta encoding ──────────────────────────────────────────────────────
    enc = encoding or detect_encoding(raw_content)

    try:
        text = raw_content.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError) as exc:
        logger.error(
            "Falha ao decodificar CSV com encoding '%s': %s. Tentando latin-1.", enc, exc
        )
        enc = "latin-1"
        text = raw_content.decode(enc, errors="replace")

    # ── Detecta delimitador ───────────────────────────────────────────────────
    delim = delimiter or detect_delimiter(text)

    # ── Lê com pandas ─────────────────────────────────────────────────────────
    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=delim,
            skiprows=skip_rows,
            dtype=str,              # todo conteúdo como string; parsers convertem
            keep_default_na=False,  # evita NaN automático (ex: "N/A" nas descrições)
            on_bad_lines="warn",    # linhas malformadas geram warning, não erro
        )

        # Remove colunas e linhas completamente vazias
        df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")

        # Strip em todos os nomes de colunas
        df.columns = [str(c).strip() for c in df.columns]

        logger.debug(
            "CSV lido: %d linhas × %d colunas (enc=%s, delim=%r)",
            len(df), len(df.columns), enc, delim,
        )

        return CsvReadResult(
            dataframe=df,
            detected_encoding=enc,
            detected_delimiter=delim,
            header_row=list(df.columns),
        )

    except Exception as exc:
        logger.error("Erro inesperado ao ler CSV: %s", exc, exc_info=True)
        return CsvReadResult(
            dataframe=pd.DataFrame(),
            detected_encoding=enc,
            detected_delimiter=delim,
            header_row=[],
        )
