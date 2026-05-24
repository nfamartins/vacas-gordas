"""Página de Dashboard — resumo financeiro com gráficos e métricas."""
from __future__ import annotations

from datetime import datetime, date

import streamlit as st

from database.repositories.transaction_repo import transaction_repo
from ui.components.charts import bar_by_category, line_by_month, pie_by_category

_MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def render() -> None:
    """Renderiza a página de Dashboard."""
    st.title("📊 Dashboard")

    # ── Filtros de período ─────────────────────────────────────────────────────
    hoje = date.today()
    col_ano, col_mes, _ = st.columns([1, 2, 5])

    with col_ano:
        ano = st.selectbox(
            "Ano",
            options=list(range(hoje.year, hoje.year - 5, -1)),
            index=0,
            key="dash_ano",
        )

    with col_mes:
        mes_idx = st.selectbox(
            "Mês",
            options=list(range(1, 13)),
            format_func=lambda m: _MESES[m - 1],
            index=hoje.month - 1,
            key="dash_mes",
        )

    # Define o intervalo do período selecionado
    start = datetime(ano, mes_idx, 1)
    last_day = _last_day_of_month(ano, mes_idx)
    end = datetime(ano, mes_idx, last_day, 23, 59, 59)

    # ── Resumo do período ───────────────────────��──────────────────────────────
    st.divider()

    try:
        summary = transaction_repo.get_period_summary(start, end)
    except Exception as exc:
        st.error(f"Erro ao carregar resumo: {exc}")
        return

    total_gasto = summary["total_gasto"]
    total_recebido = summary["total_recebido"]
    saldo = summary["saldo"]
    count = summary["count"]

    if count == 0:
        _render_empty_state()
        return

    # Cards de resumo
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "💸 Total Gasto",
        _fmt_brl(total_gasto),
        delta=None,
    )
    c2.metric(
        "💰 Total Recebido",
        _fmt_brl(total_recebido),
        delta=None,
    )
    c3.metric(
        "📈 Saldo do Período",
        _fmt_brl(saldo),
        delta=f"{'positivo' if saldo >= 0 else 'negativo'}",
        delta_color="normal" if saldo >= 0 else "inverse",
    )
    c4.metric(
        "🔢 Transações",
        count,
    )

    st.divider()

    # ── Gráficos ────────────────────��──────────────────────────────────���───────
    try:
        category_data = transaction_repo.aggregate_by_category(start, end)
    except Exception as exc:
        st.error(f"Erro ao carregar dados de categorias: {exc}")
        category_data = []

    try:
        month_data = transaction_repo.aggregate_by_month(ano)
    except Exception as exc:
        st.error(f"Erro ao carregar dados mensais: {exc}")
        month_data = []

    # Linha 1: pizza + barras
    col_pizza, col_barras = st.columns([1, 1])

    with col_pizza:
        st.plotly_chart(
            pie_by_category(category_data),
            use_container_width=True,
        )

    with col_barras:
        st.plotly_chart(
            bar_by_category(category_data),
            use_container_width=True,
        )

    # Linha 2: evolução mensal (largura total)
    st.plotly_chart(
        line_by_month(month_data),
        use_container_width=True,
    )


# ── Helpers ──────────────────────��───────────────────────��─────────────────────

def _render_empty_state() -> None:
    """Exibe mensagem orientando o usuário quando não há dados."""
    st.info(
        "📂 **Nenhuma transação encontrada para o período selecionado.**\n\n"
        "Para começar, acesse **Importar** no menu lateral e faça o upload "
        "de um extrato ou fatura do seu banco."
    )


def _last_day_of_month(year: int, month: int) -> int:
    """Retorna o último dia do mês."""
    import calendar
    return calendar.monthrange(year, month)[1]


def _fmt_brl(value: float) -> str:
    """Formata valor como 'R$ 1.234,56'."""
    return f"R$ {abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
