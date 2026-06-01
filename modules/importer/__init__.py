"""Orquestrador do módulo de importação de arquivos financeiros.

Responsabilidades:
- Manter o registry de parsers disponíveis.
- Detectar automaticamente o parser correto para um arquivo.
- Executar o fluxo completo de importação: parse → normalização → retorno.

Fluxo principal:
    1. detect_parser(filename, content) → identifica o parser.
    2. import_file(filename, content, ...) → executa parse + normalização.

Adicionar novo banco:
    1. Criar modules/importer/banks/novo_banco.py com subclasse de BaseParser.
    2. Importar e adicionar à lista _PARSER_REGISTRY abaixo.
"""
from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId

from modules.importer.base_parser import BaseParser, RawTransaction
from modules.importer.banks.bb_ofx import BancoDoBrasilOfxParser
from modules.importer.banks.c6_csv import C6CsvParser
from modules.importer.banks.generic import GenericCsvParser, ColumnMapping, PreviewResult
from modules.importer.banks.nubank_csv import NubankCsvParser
from modules.importer.banks.nubank_pdf import NubankPdfParser
from modules.importer.normalizer import build_transaction_document

logger = logging.getLogger(__name__)

# ─── Registry de parsers ──────────────────────────────────────────────────────
# Ordem importa: parsers mais específicos primeiro.
# GenericCsvParser NÃO está nesta lista — nunca é selecionado automaticamente.
_PARSER_REGISTRY: list[type[BaseParser]] = [
    NubankPdfParser,           # PDF + identificação Nubank no conteúdo
    NubankCsvParser,           # CSV com colunas {date, title, amount}
    C6CsvParser,               # CSV com colunas C6 (fatura ou extrato)
    BancoDoBrasilOfxParser,    # OFX/QFX com identificador Banco do Brasil
]

# Exporta classes principais para uso externo
__all__ = [
    "detect_parser",
    "import_file",
    "BaseParser",
    "RawTransaction",
    "GenericCsvParser",
    "ColumnMapping",
    "PreviewResult",
    "C6CsvParser",
    "NubankPdfParser",
    "NubankCsvParser",
    "BancoDoBrasilOfxParser",
]


# ─── Funções públicas ─────────────────────────────────────────────────────────

def detect_parser(filename: str, raw_content: bytes) -> type[BaseParser] | None:
    """
    Percorre o registry e retorna a primeira classe de parser que reconhece o arquivo.

    Nunca instancia os parsers — chama apenas o classmethod can_parse().
    Retorna None se nenhum parser reconhecer o arquivo (use GenericCsvParser nesse caso).

    Args:
        filename:    Nome do arquivo (com extensão).
        raw_content: Conteúdo binário do arquivo.

    Returns:
        Classe do parser (não instância) ou None.
    """
    for parser_class in _PARSER_REGISTRY:
        try:
            if parser_class.can_parse(filename, raw_content):
                logger.debug(
                    "Parser detectado para '%s': %s (%s)",
                    filename, parser_class.parser_id, parser_class.institution,
                )
                return parser_class
        except Exception as exc:
            logger.warning(
                "Erro em can_parse() do parser %s para '%s': %s",
                parser_class.__name__, filename, exc,
            )

    logger.info(
        "Nenhum parser específico reconheceu '%s'. "
        "Use GenericCsvParser para mapeamento manual.",
        filename,
    )
    return None


def import_file(
    filename: str,
    raw_content: bytes,
    account_id_str: str,
    import_id_str: str,
    parser_class: type[BaseParser] | None = None,
    payment_date: str | None = None,
) -> dict[str, Any]:
    """
    Executa o fluxo completo de importação de um arquivo financeiro.

    Fluxo:
    1. Detecta o parser (ou usa o fornecido explicitamente).
    2. Instancia e executa parse() → lista de RawTransaction.
    3. Normaliza cada transação → documento MongoDB.
    4. Retorna o resultado com estatísticas.

    Args:
        filename:      Nome do arquivo (com extensão).
        raw_content:   Conteúdo binário do arquivo.
        account_id_str: ID da conta como string (será convertido para ObjectId).
        import_id_str:  ID da importação como string (será convertido para ObjectId).
        parser_class:  Classe do parser a usar (se None, detecta automaticamente).

    Returns:
        Dicionário com:
        - parser_id (str): identificador do parser usado.
        - institution (str): nome da instituição.
        - total_parsed (int): total de transações extraídas com sucesso.
        - total_errors (int): total de linhas com erro (ignoradas).
        - transactions (list[dict]): documentos MongoDB prontos para inserção.

    Raises:
        ValueError: se nenhum parser for detectado e parser_class for None.
        ValueError: se account_id_str ou import_id_str não forem ObjectId válidos.
    """
    # Converte IDs para ObjectId
    try:
        account_id = ObjectId(account_id_str)
    except Exception as exc:
        raise ValueError(f"account_id_str inválido: '{account_id_str}'") from exc

    try:
        import_id = ObjectId(import_id_str)
    except Exception as exc:
        raise ValueError(f"import_id_str inválido: '{import_id_str}'") from exc

    # Resolve o parser
    resolved_class = parser_class or detect_parser(filename, raw_content)

    if resolved_class is None:
        raise ValueError(
            f"Nenhum parser reconheceu o arquivo '{filename}'. "
            "Passe parser_class=GenericCsvParser e configure column_mapping, "
            "ou use import_file_generic() para mapeamento interativo."
        )

    # Instancia e executa o parser
    parser_instance = resolved_class()
    parser_id = resolved_class.parser_id
    institution = resolved_class.institution

    logger.info(
        "Iniciando importação de '%s' com parser '%s' (%s).",
        filename, parser_id, institution,
    )

    try:
        raw_transactions: list[RawTransaction] = parser_instance.parse(filename, raw_content)
    except Exception as exc:
        logger.error(
            "Erro fatal no parser '%s' ao processar '%s': %s",
            parser_id, filename, exc, exc_info=True,
        )
        return {
            "parser_id": parser_id,
            "institution": institution,
            "total_parsed": 0,
            "total_errors": 1,
            "transactions": [],
        }

    # Normaliza cada transação
    documents: list[dict] = []
    normalization_errors = 0

    for raw in raw_transactions:
        try:
            doc = build_transaction_document(
                raw=raw,
                account_id=account_id,
                import_id=import_id,
                source_file=filename,
                parser_id=parser_id,
                payment_date=payment_date,
            )
            documents.append(doc)
        except Exception as exc:
            normalization_errors += 1
            logger.error(
                "Erro ao normalizar transação '%s' em %s: %s",
                raw.description, raw.date, exc, exc_info=True,
            )

    total_errors = normalization_errors
    # Nota: erros de parse individual já são contabilizados dentro do parser.
    # Aqui contamos apenas erros de normalização.

    logger.info(
        "Importação de '%s' concluída: %d transação(ões), %d erro(s).",
        filename, len(documents), total_errors,
    )

    return {
        "parser_id": parser_id,
        "institution": institution,
        "total_parsed": len(documents),
        "total_errors": total_errors,
        "transactions": documents,
    }


def import_file_generic(
    filename: str,
    raw_content: bytes,
    account_id_str: str,
    import_id_str: str,
    column_mapping: ColumnMapping,
) -> dict[str, Any]:
    """
    Importa um arquivo usando o GenericCsvParser com mapeamento manual.

    Atalho para uso direto da página de importação no Streamlit quando
    detect_parser() retorna None e o usuário já definiu o mapeamento.

    Args:
        filename:       Nome do arquivo.
        raw_content:    Conteúdo binário do arquivo CSV.
        account_id_str: ID da conta como string.
        import_id_str:  ID da importação como string.
        column_mapping: Mapeamento de colunas definido pelo usuário.

    Returns:
        Mesmo formato que import_file().
    """
    try:
        account_id = ObjectId(account_id_str)
        import_id = ObjectId(import_id_str)
    except Exception as exc:
        raise ValueError(f"ID inválido: {exc}") from exc

    parser = GenericCsvParser()
    raw_transactions = parser.parse(filename, raw_content, column_mapping=column_mapping)

    documents: list[dict] = []
    normalization_errors = 0

    for raw in raw_transactions:
        try:
            doc = build_transaction_document(
                raw=raw,
                account_id=account_id,
                import_id=import_id,
                source_file=filename,
                parser_id=GenericCsvParser.parser_id,
            )
            documents.append(doc)
        except Exception as exc:
            normalization_errors += 1
            logger.error(
                "Erro ao normalizar transação genérica '%s': %s",
                raw.description, exc, exc_info=True,
            )

    logger.info(
        "Importação genérica de '%s': %d transação(ões), %d erro(s).",
        filename, len(documents), normalization_errors,
    )

    return {
        "parser_id": GenericCsvParser.parser_id,
        "institution": GenericCsvParser.institution,
        "total_parsed": len(documents),
        "total_errors": normalization_errors,
        "transactions": documents,
    }
