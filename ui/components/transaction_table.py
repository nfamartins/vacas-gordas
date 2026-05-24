"""Tabela interativa de lançamentos financeiros para uso em múltiplas páginas."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def _fmt_date(date_str: str) -> str:
    """Converte YYYY-MM-DD para DD/MM/YYYY."""
    try:
        y, m, d = date_str.split("-")
        return f"{d}/{m}/{y}"
    except Exception:
        return date_str


def _fmt_brl(value: float) -> str:
    """Formata valor como 'R$ 1.234,56' (sem sinal — a cor indica a direção)."""
    abs_val = abs(value)
    return f"R$ {abs_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def render_table(
    transactions: list[dict],
    editable: bool = False,
    on_edit: callable | None = None,
    on_ignore: callable | None = None,
) -> None:
    """
    Renderiza uma tabela de transações formatada.

    - Datas no formato DD/MM/YYYY.
    - Valores como R$ X.XXX,XX com cor: vermelho=débito, verde=crédito.
    - Se editable=True, mostra botões de ação por linha.

    Args:
        transactions: Lista de documentos de transação (dicts do MongoDB).
        editable:     Se True, exibe botões "Editar" e "Ignorar" por linha.
        on_edit:      Callback chamado com o dict da transação ao clicar em Editar.
        on_ignore:    Callback chamado com o id da transação ao clicar em Ignorar.
    """
    if not transactions:
        st.info("Nenhuma transação encontrada.")
        return

    if editable:
        _render_editable_table(transactions, on_edit, on_ignore)
    else:
        _render_static_table(transactions)


# ── Tabela estática (somente leitura) ─────────────────────────────────────────

def _render_static_table(transactions: list[dict]) -> None:
    """Exibe transações em dataframe estilizado sem controles de edição."""
    rows = []
    for tx in transactions:
        valor = float(tx.get("amount", 0))
        rows.append({
            "Data": _fmt_date(tx.get("date", "")),
            "Descrição": tx.get("description", ""),
            "Valor": _fmt_brl(valor),
            "Tipo": "⬇ Débito" if tx.get("type") == "debit" else "⬆ Crédito",
            "Categoria": tx.get("category", {}).get("full_path") or "—",
            "Status": _status_label(tx.get("categorization", {}).get("status", "")),
        })

    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Valor": st.column_config.TextColumn("Valor", width="small"),
            "Tipo": st.column_config.TextColumn("Tipo", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
        },
    )


# ── Tabela editável ───────────────────────────────────────────────────────────

def _render_editable_table(
    transactions: list[dict],
    on_edit: callable | None,
    on_ignore: callable | None,
) -> None:
    """
    Exibe transações com botões de ação por linha.

    Cada linha tem: Data | Descrição | Valor | Categoria | [Editar] [Ignorar]
    """
    header = st.columns([1, 4, 1.5, 2.5, 1, 1])
    header[0].markdown("**Data**")
    header[1].markdown("**Descrição**")
    header[2].markdown("**Valor**")
    header[3].markdown("**Categoria**")
    header[4].markdown("")
    header[5].markdown("")

    st.divider()

    for tx in transactions:
        tx_id = tx.get("_id", "")
        valor = float(tx.get("amount", 0))
        tipo = tx.get("type", "debit")
        categoria = tx.get("category", {}).get("full_path") or "—"

        # Cor do valor
        valor_str = _fmt_brl(valor)
        valor_colored = (
            f":red[{valor_str}]" if tipo == "debit"
            else f":green[{valor_str}]"
        )

        cols = st.columns([1, 4, 1.5, 2.5, 1, 1])
        cols[0].write(_fmt_date(tx.get("date", "")))
        cols[1].write(tx.get("description", "")[:60])
        cols[2].markdown(valor_colored)
        cols[3].write(categoria)

        if on_edit and cols[4].button("✏️", key=f"edit_{tx_id}", help="Editar categoria"):
            on_edit(tx)

        if on_ignore and cols[5].button(
            "🚫", key=f"ignore_{tx_id}",
            help="Marcar como ignorada",
        ):
            on_ignore(tx_id)


# ── Utilitários ───────────────────────────────────────────────────────────────

def _status_label(status: str) -> str:
    """Converte status interno para label amigável com emoji."""
    return {
        "pending": "⏳ Pendente",
        "confirmed": "✅ Confirmado",
        "ignored": "🚫 Ignorado",
        "auto": "🤖 Automático",
    }.get(status, status)
