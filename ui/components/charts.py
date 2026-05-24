"""Componentes de gráficos reutilizáveis com Plotly.

Todas as funções recebem dados já agregados pelos repositories e retornam
objetos go.Figure prontos para exibição com st.plotly_chart().

Convenções:
- Valores monetários exibidos como R$ X.XXX,XX
- Tooltips em português
- Cores consistentes entre gráficos (paleta "Pastel")
"""
from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go


# ── Paleta de cores consistente ───────────────────────────────────────────────
_COLORS = px.colors.qualitative.Pastel

_LAYOUT_DEFAULTS = dict(
    font_family="sans-serif",
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=40, b=10),
)


def _fmt_brl(value: float) -> str:
    """Formata valor como string BRL: 'R$ 1.234,56'."""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ── Funções públicas ──────────────────────────────────────────────────────────

def pie_by_category(data: list[dict]) -> go.Figure:
    """
    Gráfico de pizza de gastos agrupados por categoria nível 1.

    Args:
        data: Lista de dicts retornada por transaction_repo.aggregate_by_category().
              Campos esperados: _id (full_path), level1, total.

    Returns:
        Figura Plotly com gráfico de pizza.
    """
    if not data:
        return _empty_figure("Sem dados de categorias no período")

    # Agrupa por level1 (soma subcategorias)
    level1_totals: dict[str, float] = {}
    for row in data:
        key = row.get("level1") or row.get("_id") or "Outros"
        level1_totals[key] = level1_totals.get(key, 0.0) + float(row.get("total", 0))

    labels = list(level1_totals.keys())
    values = list(level1_totals.values())

    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.35,
            marker_colors=_COLORS,
            hovertemplate=(
                "<b>%{label}</b><br>"
                "Total: R$ %{value:,.2f}<br>"
                "Participação: %{percent}<extra></extra>"
            ),
            textinfo="label+percent",
        )
    )
    fig.update_layout(
        title_text="Gastos por Categoria",
        showlegend=True,
        legend=dict(orientation="v", x=1.02),
        **_LAYOUT_DEFAULTS,
    )
    return fig


def bar_by_category(data: list[dict]) -> go.Figure:
    """
    Gráfico de barras horizontais de gastos por subcategoria (nível 2).

    As barras são ordenadas por valor decrescente.

    Args:
        data: Lista de dicts com _id (full_path), total, count.

    Returns:
        Figura Plotly com barras horizontais.
    """
    if not data:
        return _empty_figure("Sem dados de subcategorias no período")

    sorted_data = sorted(data, key=lambda r: r.get("total", 0), reverse=True)

    labels = [r.get("_id") or "Sem categoria" for r in sorted_data]
    values = [float(r.get("total", 0)) for r in sorted_data]
    counts = [int(r.get("count", 0)) for r in sorted_data]

    fig = go.Figure(
        go.Bar(
            y=labels,
            x=values,
            orientation="h",
            marker_color=_COLORS[0],
            customdata=counts,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Total: R$ %{x:,.2f}<br>"
                "Transações: %{customdata}<extra></extra>"
            ),
            text=[_fmt_brl(v) for v in values],
            textposition="outside",
        )
    )
    fig.update_layout(
        title_text="Gastos por Subcategoria",
        xaxis_title="Valor (R$)",
        yaxis_autorange="reversed",
        height=max(300, len(labels) * 35),
        **_LAYOUT_DEFAULTS,
    )
    return fig


def line_by_month(data: list[dict]) -> go.Figure:
    """
    Gráfico de linha com evolução mensal de gastos.

    Args:
        data: Lista de dicts retornada por transaction_repo.aggregate_by_month().
              Campos esperados: _id ("YYYY-MM"), total.

    Returns:
        Figura Plotly com gráfico de linha.
    """
    if not data:
        return _empty_figure("Sem dados mensais disponíveis")

    months = [r["_id"] for r in data]
    values = [float(r.get("total", 0)) for r in data]

    # Labels mais legíveis: "2024-01" → "Jan/24"
    _MONTH_NAMES = [
        "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
        "Jul", "Ago", "Set", "Out", "Nov", "Dez",
    ]
    display_labels = []
    for m in months:
        try:
            year, month = m.split("-")
            display_labels.append(f"{_MONTH_NAMES[int(month) - 1]}/{year[2:]}")
        except Exception:
            display_labels.append(m)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=display_labels,
        y=values,
        mode="lines+markers+text",
        line=dict(color=_COLORS[1], width=2),
        marker=dict(size=8),
        text=[_fmt_brl(v) for v in values],
        textposition="top center",
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Gasto: R$ %{y:,.2f}<extra></extra>"
        ),
        name="Gastos",
    ))
    fig.update_layout(
        title_text="Evolução Mensal de Gastos",
        yaxis_title="Valor (R$)",
        xaxis_title="Mês",
        **_LAYOUT_DEFAULTS,
    )
    return fig


# ── Utilitário interno ────────────────────────────────────────────────────────

def _empty_figure(message: str) -> go.Figure:
    """Retorna uma figura vazia com mensagem centralizada."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(size=14, color="gray"),
    )
    fig.update_layout(
        xaxis_visible=False,
        yaxis_visible=False,
        height=250,
        **_LAYOUT_DEFAULTS,
    )
    return fig
