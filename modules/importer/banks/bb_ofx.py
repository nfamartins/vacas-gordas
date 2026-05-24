"""Parser para extratos OFX do Banco do Brasil.

Utiliza a biblioteca ofxparse via o utilitário ofx_reader.py para extrair
transações do formato OFX 1.x (SGML) exportado pelo Banco do Brasil.

Particularidades do BB:
- Arquivos OFX 1.x com charset cp1252 (Windows Latin).
- BANKID "001" ou texto "BANCO DO BRASIL" no conteúdo.
- MEMO pode conter HTML entities (&amp;, &gt;, etc.) — tratado pelo ofx_reader.
- FITID único por transação → preservado no raw_text para dedup mais forte.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from modules.importer.base_parser import BaseParser, RawTransaction
from modules.importer.ofx_reader import OfxReadResult, read_ofx_bytes, ofx_amount_to_type

logger = logging.getLogger(__name__)

# Identificadores do Banco do Brasil em arquivos OFX
_BB_IDENTIFIERS: list[bytes] = [
    b"BANCO DO BRASIL",
    b"Banco do Brasil",
    b"<BANKID>001",       # código BACEN do Banco do Brasil
    b"<FID>001",
    b"BB.COM.BR",
]


class BancoDoBrasilOfxParser(BaseParser):
    """
    Parser para extratos OFX do Banco do Brasil.

    A detecção combina a extensão do arquivo (.ofx/.qfx) com a presença
    de identificadores do BB no conteúdo, garantindo que outros arquivos OFX
    de outros bancos não sejam capturados por este parser.
    """

    parser_id = "bb_ofx"
    institution = "Banco do Brasil"

    @classmethod
    def can_parse(cls, filename: str, raw_content: bytes) -> bool:
        """
        Retorna True se o arquivo é reconhecido como extrato OFX do Banco do Brasil.

        Critérios (AMBOS devem ser satisfeitos):
        1. Extensão do arquivo é .ofx ou .qfx.
        2. Conteúdo contém pelo menos um identificador do BB OU nome do arquivo
           contém "bb", "bancodobrasil" ou "banco_do_brasil".
        """
        # Critério 1: extensão
        if not re.search(r"\.(ofx|qfx)$", filename, re.IGNORECASE):
            return False

        # Critério 2a: nome do arquivo
        filename_match = bool(
            re.search(r"\bbb\b|banco.?do.?brasil|bancobrasil", filename, re.IGNORECASE)
        )
        if filename_match:
            return True

        # Critério 2b: conteúdo (verifica os primeiros 2 KB — o header OFX)
        header_sample = raw_content[:2_048]
        for identifier in _BB_IDENTIFIERS:
            if identifier in header_sample:
                return True

        # Busca mais ampla no corpo do arquivo (até 10 KB)
        body_sample = raw_content[:10_240]
        return b"BANCO DO BRASIL" in body_sample or b"Banco do Brasil" in body_sample

    def parse(self, filename: str, raw_content: bytes) -> list[RawTransaction]:
        """Extrai transações do extrato OFX do Banco do Brasil."""
        ofx_result: OfxReadResult = read_ofx_bytes(raw_content)

        if not ofx_result.transactions:
            logger.warning(
                "BB OFX '%s': nenhuma transação encontrada no arquivo.",
                filename,
            )
            return []

        transactions: list[RawTransaction] = []

        for idx, ofx_tx in enumerate(ofx_result.transactions):
            try:
                amount_abs, tx_type = ofx_amount_to_type(ofx_tx.amount, ofx_tx.trntype)

                # Descrição: usa MEMO; fallback para TRNTYPE se vazio
                description = ofx_tx.memo.strip()
                if not description:
                    description = f"Lançamento {ofx_tx.trntype}" if ofx_tx.trntype else "(sem descrição)"

                # raw_text inclui o FITID para deduplicação mais forte
                raw_text = self._build_raw_text(ofx_tx)

                transactions.append(RawTransaction(
                    date=ofx_tx.date.strftime("%Y-%m-%d"),
                    description=description,
                    amount=amount_abs,
                    type=tx_type,
                    currency=ofx_result.currency or "BRL",
                    raw_text=raw_text,
                ))

            except Exception as exc:
                logger.error(
                    "BB OFX '%s', transação #%d ignorada: %s",
                    filename, idx, exc, exc_info=True,
                )

        logger.info(
            "BB OFX '%s': %d transação(ões) extraída(s) "
            "(conta: %s, moeda: %s).",
            filename,
            len(transactions),
            ofx_result.account_id or "desconhecida",
            ofx_result.currency,
        )
        return transactions

    # ── Métodos internos ──────────────────────────────────────────────────────

    def _build_raw_text(self, ofx_tx) -> str:
        """
        Monta string de rastreabilidade com os campos chave do OFX.

        O FITID é preservado aqui para possibilitar deduplicação mais forte
        no repositório (comparação direta de FITID além da dedup_key SHA-256).
        """
        parts = [f"FITID:{ofx_tx.fitid}"]

        if ofx_tx.trntype:
            parts.append(f"TRNTYPE:{ofx_tx.trntype}")

        if ofx_tx.memo:
            parts.append(f"MEMO:{ofx_tx.memo}")

        if ofx_tx.checknum:
            parts.append(f"CHECKNUM:{ofx_tx.checknum}")

        return " | ".join(parts)
