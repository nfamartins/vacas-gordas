"""Página de listagem completa de transações com filtros e exportação."""
from __future__ import annotations

import io
import logging
from datetime import date, datetime

import pandas as pd
import streamlit as st

from database.repositories.account_repo import account_repo
from database.repositories.category_repo import category_repo
from database.repositories.transaction_repo import transaction_repo

logger = logging.getLogger(__name__)

_MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def render() -> None:
    """Renderiza a página de transações com filtros avançados."""
    st.title("💳 Transações")

    # ── Filtros ───────────────────────────────────────────────────────────────
    with st.expander("🔍 Filtros", expanded=True):
        filtros = _render_filters()

    st.divider()

    # ── Busca com filtros ─────────────────────────────────────────────────────
    try:
        transactions = transaction_repo.find_filtered(
            start=filtros.get("start"),
            end=filtros.get("end"),
            account_id=filtros.get("account_id"),
            category_path=filtros.get("category_path"),
            status=filtros.get("status"),
            tx_type=filtros.get("tx_type"),
            limit=500,
        )
    except Exception as exc:
        st.error(f"Erro ao buscar transações: {exc}")
        return

    if not transactions:
        st.info("Nenhuma transação encontrada com os filtros selecionados.")
        return

    # ── Resumo rápido ─────────────────────────────────────────────────────────
    total_debito = sum(abs(float(t.get("amount", 0))) for t in transactions if t.get("type") == "debit")
    total_credito = sum(abs(float(t.get("amount", 0))) for t in transactions if t.get("type") == "credit")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total de registros", len(transactions))
    c2.metric("💸 Débitos", _fmt_brl(total_debito))
    c3.metric("💰 Créditos", _fmt_brl(total_credito))

    st.divider()

    # ── Tabela ────────────────────────────────────────────────────────────────
    df = _transactions_to_df(transactions)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Edição de categoria ────────────────────────────────────────────────────
    _render_edit_section(transactions)

    # ── Exportação ────────────────────────────────────────────────────────────
    st.divider()
    _render_export(df)


# ── Filtros ────────────────────────────────────────────────────────────────────

def _render_filters() -> dict:
    """Renderiza os controles de filtro e retorna um dict com os valores."""
    hoje = date.today()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        ano = st.selectbox(
            "Ano",
            options=list(range(hoje.year, hoje.year - 5, -1)),
            index=0,
            key="tx_ano",
        )
        mes = st.selectbox(
            "Mês",
            options=[0] + list(range(1, 13)),
            format_func=lambda m: "Todos" if m == 0 else _MESES[m - 1],
            index=hoje.month,
            key="tx_mes",
        )

    with col2:
        # Seletor de conta
        accounts = account_repo.find_active()
        acc_options = {"Todas as contas": None}
        acc_options.update({a["name"]: a["_id"] for a in accounts})
        selected_acc = st.selectbox("Conta", list(acc_options.keys()), key="tx_conta")
        account_id = acc_options[selected_acc]

        # Tipo
        tipo_options = {"Todos": None, "Débito": "debit", "Crédito": "credit"}
        selected_tipo = st.selectbox("Tipo", list(tipo_options.keys()), key="tx_tipo")
        tx_type = tipo_options[selected_tipo]

    with col3:
        # Categoria
        try:
            all_cats = category_repo.find_all()
            cat_paths = ["Todas"] + [c["full_path"] for c in all_cats if c.get("full_path")]
        except Exception:
            cat_paths = ["Todas"]

        selected_cat = st.selectbox("Categoria", cat_paths, key="tx_cat")
        category_path = None if selected_cat == "Todas" else selected_cat

    with col4:
        # Status
        status_options = {
            "Todos": None,
            "Pendente": "pending",
            "Confirmado": "confirmed",
            "Ignorado": "ignored",
            "Automático": "auto",
        }
        selected_status = st.selectbox("Status", list(status_options.keys()), key="tx_status")
        status = status_options[selected_status]

    # Calcula datas
    if mes == 0:
        start_str = f"{ano}-01-01"
        end_str = f"{ano}-12-31"
    else:
        import calendar
        last_day = calendar.monthrange(ano, mes)[1]
        start_str = f"{ano}-{mes:02d}-01"
        end_str = f"{ano}-{mes:02d}-{last_day:02d}"

    return {
        "start": start_str,
        "end": end_str,
        "account_id": account_id,
        "category_path": category_path,
        "status": status,
        "tx_type": tx_type,
    }


# ── Tabela ─────────────────────────────────────────────────────────────────────

def _transactions_to_df(transactions: list[dict]) -> pd.DataFrame:
    """Converte lista de transações para DataFrame formatado."""
    rows = []
    for tx in transactions:
        valor = float(tx.get("amount", 0))
        tipo = tx.get("type", "debit")
        rows.append({
            "Data": _fmt_date(tx.get("date", "")),
            "Descrição": tx.get("description", "")[:60],
            "Valor": _fmt_brl(abs(valor)),
            "Tipo": "⬇ Débito" if tipo == "debit" else "⬆ Crédito",
            "Categoria": tx.get("category", {}).get("full_path") or "—",
            "Status": _status_label(tx.get("categorization", {}).get("status", "")),
            "_id": tx.get("_id", ""),
        })

    df = pd.DataFrame(rows)
    return df.drop(columns=["_id"])


# ── Edição de categoria ────────────────────────────────────────────────────────

def _render_edit_section(transactions: list[dict]) -> None:
    """Permite editar a categoria de uma transação já confirmada."""
    with st.expander("✏️ Editar categoria de uma transação"):
        ids = {
            f"{t.get('date')} — {t.get('description', '')[:40]}": t.get("_id")
            for t in transactions
        }
        selected_label = st.selectbox("Transação", list(ids.keys()), key="tx_edit_sel")
        selected_id = ids.get(selected_label)

        if not selected_id:
            return

        try:
            tree = category_repo.build_tree()
        except Exception:
            tree = []

        cat_map = {cat["name"]: cat.get("children", []) for cat in tree}
        l1_names = list(cat_map.keys())

        col1, col2 = st.columns(2)
        new_l1 = col1.selectbox("Nova Categoria", ["— Selecione —"] + l1_names, key="edit_l1")
        l2_opts = cat_map.get(new_l1, []) if new_l1 != "— Selecione —" else []
        l2_names = [c["name"] for c in l2_opts]
        new_l2 = col2.selectbox("Nova Subcategoria", ["—"] + l2_names, key="edit_l2")

        if st.button("Salvar edição", key="btn_save_edit"):
            if new_l1 == "— Selecione —":
                st.warning("Selecione uma categoria.")
                return
            l2_doc = next((c for c in l2_opts if c["name"] == new_l2), None)
            category = {
                "level1_id": None,
                "level1_name": new_l1,
                "level2_id": l2_doc["_id"] if l2_doc else None,
                "level2_name": new_l2 if l2_doc else None,
                "full_path": (f"{new_l1} > {new_l2}" if l2_doc else new_l1),
            }
            try:
                transaction_repo.update_category(selected_id, category, method="manual")
                st.success("Categoria atualizada!")
                st.rerun()
            except Exception as exc:
                st.error(f"Erro ao salvar: {exc}")


# ── Exportação ─────────────────────────────────────────────────────────────────

def _render_export(df: pd.DataFrame) -> None:
    """Botão de exportação do DataFrame para CSV."""
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, sep=";", decimal=",", encoding="utf-8-sig")

    st.download_button(
        label="📥 Exportar para CSV",
        data=csv_buffer.getvalue().encode("utf-8-sig"),
        file_name="vacas_gordas_transacoes.csv",
        mime="text/csv",
    )


# ── Utilitários ────────────────────────────────────────────────────────────────

def _fmt_date(date_str: str) -> str:
    try:
        y, m, d = date_str.split("-")
        return f"{d}/{m}/{y}"
    except Exception:
        return date_str


def _fmt_brl(value: float) -> str:
    return f"R$ {abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _status_label(status: str) -> str:
    return {
        "pending": "⏳ Pendente",
        "confirmed": "✅ Confirmado",
        "ignored": "🚫 Ignorado",
        "auto": "🤖 Automático",
    }.get(status, status)
