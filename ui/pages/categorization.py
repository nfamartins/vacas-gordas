"""Página de revisão e confirmação interativa de categorias.

Fluxo:
1. Mostra transações com status 'pending'.
2. Para cada uma, exibe um seletor flat de categoria (full_path completo).
   Suporta N níveis de profundidade.
3. Ao confirmar, salva a categoria na transação e cria uma regra automática.
4. Botão "Categorizar com IA" reservado para integração futura.
"""
from __future__ import annotations

import logging

import streamlit as st

from database.repositories.category_repo import category_repo
from database.repositories.rule_repo import rule_repo
from database.repositories.transaction_repo import transaction_repo
from modules.importer.normalizer import normalize_description

logger = logging.getLogger(__name__)


def render() -> None:
    """Renderiza a página de categorização interativa."""
    st.title("🏷️ Categorizar Transações")

    try:
        pending = transaction_repo.find_pending()
        # Carrega todas as categorias selecionáveis (nível ≥ 2 — exclui raízes)
        all_cats = [
            c for c in category_repo.find_all(active_only=True)
            if c.get("level", 1) >= 2
        ]
    except Exception as exc:
        st.error(f"Erro ao carregar dados: {exc}")
        return

    if not all_cats:
        st.warning(
            "Nenhuma categoria encontrada. "
            "Acesse **Configurações → Banco de Dados → Inicializar Banco** para criar as categorias padrão."
        )
        return

    total_pending = len(pending)

    if total_pending == 0:
        st.success("🎉 Todas as transações já foram categorizadas!")
        return

    # Progresso
    _render_progress_bar(total_pending)

    # Botão IA (placeholder)
    _render_ai_batch_button()

    st.divider()

    # Índice {full_path: category_doc} para lookup rápido
    cat_index = {c["full_path"]: c for c in all_cats}
    cat_paths = sorted(cat_index.keys())

    # Pré-calcula default por sugestão LLM
    for tx in pending:
        _render_transaction_card(tx, cat_paths, cat_index)
        st.divider()


# ── Componentes ───────────────────────────────────────────────────────────────

def _render_progress_bar(total_pending: int) -> None:
    try:
        total_all = transaction_repo.count()
        pct = (total_all - total_pending) / total_all if total_all > 0 else 0
    except Exception:
        pct = 0

    c1, c2 = st.columns([1, 3])
    c1.metric("⏳ Pendentes", total_pending)
    c2.progress(pct, text=f"{int(pct * 100)}% categorizados")


def _render_ai_batch_button() -> None:
    col, _ = st.columns([2, 4])
    with col:
        if st.button(
            "🤖 Categorizar com IA",
            type="secondary",
            use_container_width=True,
            help="Integração LLM prevista — ainda não implementada.",
        ):
            st.info(
                "⚙️ **Categorização por IA ainda não implementada.**  \n"
                "Por enquanto, classifique manualmente usando os seletores abaixo.  \n"
                "As regras salvas ao confirmar serão usadas futuramente para classificação automática."
            )


def _render_transaction_card(
    tx: dict,
    cat_paths: list[str],
    cat_index: dict[str, dict],
) -> None:
    """
    Card de uma transação com seletor flat de categoria (full_path) e ações.

    O selectbox do Streamlit tem busca embutida, então basta o usuário
    digitar parte do caminho para filtrar (ex: "delivery", "água", "uber").
    """
    tx_id = tx.get("_id", "")
    valor = float(tx.get("amount", 0))
    tipo = tx.get("type", "debit")
    valor_str = f"R$ {abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    valor_colored = f":red[{valor_str}]" if tipo == "debit" else f":green[{valor_str}]"

    cat_obj = tx.get("categorization", {})
    llm_suggestion = cat_obj.get("llm_suggestion")
    llm_confidence = cat_obj.get("llm_confidence")

    with st.container():
        # Linha de cabeçalho
        c_date, c_desc, c_val = st.columns([1, 4, 1.5])
        c_date.markdown(f"📅 `{tx.get('date', '')}`")
        c_desc.markdown(f"**{tx.get('description', '')[:70]}**")
        c_val.markdown(valor_colored)

        # Sugestão da LLM
        if llm_suggestion:
            st.caption(
                f"🤖 Sugestão IA: **{llm_suggestion}** {_confidence_label(llm_confidence)}"
            )

        # Determina pré-seleção
        default_idx = _default_cat_index(llm_suggestion, cat_paths)

        # Seletor flat + ações
        c_cat, c_confirm, c_ignore = st.columns([6, 1, 1])

        with c_cat:
            selected_path = st.selectbox(
                "Categoria",
                ["— Selecione —"] + cat_paths,
                index=default_idx,
                key=f"cat_{tx_id}",
                label_visibility="collapsed",
                help="Digite para filtrar. Ex: 'delivery', 'uber', 'água'.",
            )

        with c_confirm:
            if st.button("✅", key=f"confirm_{tx_id}", help="Confirmar categoria"):
                if selected_path == "— Selecione —":
                    st.warning("Selecione uma categoria.")
                else:
                    _handle_confirm(tx, selected_path, cat_index)

        with c_ignore:
            if st.button("🚫", key=f"ignore_{tx_id}", help="Ignorar transação"):
                _handle_ignore(tx_id)


def _handle_confirm(tx: dict, selected_path: str, cat_index: dict[str, dict]) -> None:
    """
    Confirma a categoria de uma transação e salva a regra automaticamente.

    Preenche os campos level1/level2 do schema a partir dos componentes
    do full_path. O full_path é a referência autoritativa.
    """
    tx_id = tx.get("_id", "")
    cat_doc = cat_index.get(selected_path)

    # Extrai componentes do caminho para preencher level1/level2
    parts = selected_path.split(" > ")
    level1_name = parts[0] if parts else ""
    level2_name = parts[1] if len(parts) > 1 else None

    # Tenta obter IDs dos docs de categoria
    level1_id = None
    level2_id = None
    if cat_doc:
        # ID do próprio doc selecionado como level2 (mais específico disponível)
        # e sobe na hierarquia para level1
        level2_id = cat_doc.get("_id")
        # Busca o doc de nível 1 (raiz mais próxima da hierarquia)
        l1_doc = category_repo.find_by_path(level1_name)
        if l1_doc:
            level1_id = l1_doc["_id"]

    category = {
        "level1_id":   level1_id,
        "level1_name": level1_name,
        "level2_id":   level2_id if len(parts) > 1 else None,
        "level2_name": level2_name,
        "full_path":   selected_path,
    }

    try:
        ok = transaction_repo.update_category(tx_id, category, method="manual")
        if not ok:
            st.error("Não foi possível salvar a categoria.")
            return

        # Salva regra automática (custo zero nas próximas importações)
        desc_norm = normalize_description(tx.get("description", ""))
        if desc_norm:
            rule_repo.upsert_from_confirmation(desc_norm, category)

        st.success(f"✅ **{selected_path}**")
        st.rerun()

    except Exception as exc:
        st.error(f"Erro ao confirmar: {exc}")
        logger.error("Erro em _handle_confirm (%s): %s", tx_id, exc, exc_info=True)


def _handle_ignore(tx_id: str) -> None:
    try:
        transaction_repo.update_ignored(tx_id, ignored=True)
        st.rerun()
    except Exception as exc:
        st.error(f"Erro ao ignorar: {exc}")


# ── Utilitários ───────────────────────────────────────────────────────────────

def _default_cat_index(llm_suggestion: str | None, cat_paths: list[str]) -> int:
    """
    Retorna o índice (base 1 — considerando o placeholder "— Selecione —")
    do path que melhor corresponde à sugestão da LLM.

    Tenta match exato primeiro, depois prefix/substring.
    """
    if not llm_suggestion:
        return 0
    suggestion_lower = llm_suggestion.lower()

    # Match exato
    for i, p in enumerate(cat_paths):
        if p.lower() == suggestion_lower:
            return i + 1  # +1 pelo placeholder

    # Match por sufixo ou subcadeia
    for i, p in enumerate(cat_paths):
        if suggestion_lower in p.lower() or p.lower() in suggestion_lower:
            return i + 1

    return 0


def _confidence_label(confidence: float | None) -> str:
    if confidence is None:
        return ""
    if confidence >= 0.8:
        return "🟢 alta"
    if confidence >= 0.5:
        return "🟡 média"
    return "🔴 baixa"
