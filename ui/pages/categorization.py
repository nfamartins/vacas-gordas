"""Página de revisão e confirmação interativa de categorias.

É a página central do fluxo de uso do Vacas Gordas:
1. Mostra transações com status 'pending'.
2. Para cada uma, exibe sugestão da LLM (se houver) e seletores de categoria.
3. Ao confirmar, salva a categoria e cria uma regra automática.
4. Botão "Categorizar tudo com IA" (integração LLM — implementação futura).
"""
from __future__ import annotations

import logging

import streamlit as st

from database.repositories.category_repo import category_repo
from database.repositories.rule_repo import rule_repo
from database.repositories.transaction_repo import transaction_repo
from modules.importer.base_parser import normalize_col_name
from modules.importer.normalizer import normalize_description

logger = logging.getLogger(__name__)

_CONFIDENCE_COLORS = {
    "alta": "🟢",
    "média": "🟡",
    "baixa": "🔴",
}


def render() -> None:
    """Renderiza a página de categorização interativa."""
    st.title("🏷️ Categorizar Transações")

    # Carrega dados necessários
    try:
        pending = transaction_repo.find_pending()
        category_tree = category_repo.build_tree()
    except Exception as exc:
        st.error(f"Erro ao carregar dados: {exc}")
        return

    total_pending = len(pending)

    if total_pending == 0:
        st.success("🎉 Todas as transações já foram categorizadas!")
        return

    # Barra de progresso
    _render_progress_bar(total_pending)

    # Botão de categorização em lote por IA
    _render_ai_batch_button(pending)

    st.divider()

    # Mapa de categorias para os seletores
    cat_map = _build_category_map(category_tree)
    level1_names = list(cat_map.keys())

    # Renderiza cada transação pendente
    for tx in pending:
        _render_transaction_card(tx, cat_map, level1_names)
        st.divider()


# ── Componentes internos ──────────────────────────────────────────────────────

def _render_progress_bar(total_pending: int) -> None:
    """Exibe contador de progresso."""
    try:
        total_all = transaction_repo.count()
        categorized = total_all - total_pending
        pct = categorized / total_all if total_all > 0 else 0
    except Exception:
        categorized = 0
        pct = 0

    col_info, col_bar = st.columns([1, 3])
    with col_info:
        st.metric("⏳ Pendentes", total_pending)
    with col_bar:
        st.progress(pct, text=f"{int(pct * 100)}% categorizados")


def _render_ai_batch_button(pending: list[dict]) -> None:
    """Botão para categorizar todas as transações pendentes com IA."""
    col, _ = st.columns([2, 4])
    with col:
        if st.button(
            "🤖 Categorizar tudo com IA",
            type="secondary",
            use_container_width=True,
            help="Envia todas as transações pendentes para categorização automática pela LLM.",
        ):
            st.info(
                "⚙️ **Categorização por IA em implementação.**\n\n"
                "O módulo `modules/categorizer/llm_categorizer.py` será integrado aqui. "
                "Por enquanto, use os seletores abaixo para categorizar manualmente."
            )


def _render_transaction_card(
    tx: dict,
    cat_map: dict[str, list[dict]],
    level1_names: list[str],
) -> None:
    """
    Renderiza o card de uma transação com seletores de categoria e botões de ação.

    Args:
        tx:           Documento de transação do MongoDB (com _id como string).
        cat_map:      Mapa {level1_name: [categoria_dict, ...]} para os seletores.
        level1_names: Lista de nomes de nível 1 para o primeiro selectbox.
    """
    tx_id = tx.get("_id", "")
    valor = float(tx.get("amount", 0))
    tipo = tx.get("type", "debit")
    valor_str = f"R$ {abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    valor_colored = f":red[{valor_str}]" if tipo == "debit" else f":green[{valor_str}]"

    # Sugestão da LLM (se houver)
    cat_obj = tx.get("categorization", {})
    llm_suggestion = cat_obj.get("llm_suggestion")
    llm_confidence = cat_obj.get("llm_confidence")

    with st.container():
        # Linha de informações
        col_data, col_desc, col_valor = st.columns([1, 4, 1.5])
        col_data.markdown(f"📅 `{tx.get('date', '')}`")
        col_desc.markdown(f"**{tx.get('description', '')[:70]}**")
        col_valor.markdown(valor_colored)

        # Sugestão LLM
        if llm_suggestion:
            conf_label = _confidence_label(llm_confidence)
            st.caption(f"🤖 Sugestão IA: **{llm_suggestion}** {conf_label}")

        # Seletores de categoria
        col_l1, col_l2, col_confirm, col_ignore = st.columns([2, 2, 1, 1])

        # Pré-seleciona com base na sugestão LLM
        default_l1 = _default_level1(llm_suggestion, level1_names)

        with col_l1:
            selected_l1 = st.selectbox(
                "Categoria",
                ["— Selecione —"] + level1_names,
                index=level1_names.index(default_l1) + 1 if default_l1 else 0,
                key=f"l1_{tx_id}",
                label_visibility="collapsed",
            )

        # Subcategorias disponíveis para o nível 1 selecionado
        l2_options: list[dict] = []
        l2_names: list[str] = []
        if selected_l1 and selected_l1 != "— Selecione —":
            l2_options = cat_map.get(selected_l1, [])
            l2_names = [c["name"] for c in l2_options]

        with col_l2:
            selected_l2 = st.selectbox(
                "Subcategoria",
                ["— Selecione —"] + l2_names,
                key=f"l2_{tx_id}",
                label_visibility="collapsed",
                disabled=not l2_names,
            )

        with col_confirm:
            if st.button("✅", key=f"confirm_{tx_id}", help="Confirmar categoria"):
                _handle_confirm(tx, selected_l1, selected_l2, cat_map, l2_options)

        with col_ignore:
            if st.button("🚫", key=f"ignore_{tx_id}", help="Ignorar transação"):
                _handle_ignore(tx_id)


def _handle_confirm(
    tx: dict,
    selected_l1: str,
    selected_l2: str,
    cat_map: dict[str, list[dict]],
    l2_options: list[dict],
) -> None:
    """
    Confirma a categoria de uma transação e salva a regra automaticamente.

    Args:
        tx:         Documento da transação.
        selected_l1: Nome da categoria nível 1 selecionada.
        selected_l2: Nome da subcategoria nível 2 selecionada.
        cat_map:    Mapa de categorias (para buscar os IDs).
        l2_options: Lista de subcategorias disponíveis.
    """
    if not selected_l1 or selected_l1 == "— Selecione —":
        st.warning("Selecione uma categoria antes de confirmar.")
        return

    tx_id = tx.get("_id", "")

    # Busca o objeto category nível 1
    l1_cats = [
        c for cats in cat_map.values() for c in cats
        if False  # não; l1 não está no cat_map dessa forma
    ]
    # cat_map é {l1_name: [l2_docs]}; precisamos buscar o doc de l1 separado
    try:
        l1_doc = category_repo.find_by_path(selected_l1)
        if not l1_doc:
            # Tenta buscar pelo nome diretamente
            all_cats = category_repo.find_level1()
            l1_doc = next((c for c in all_cats if c["name"] == selected_l1), None)
    except Exception:
        l1_doc = None

    # Busca o objeto category nível 2
    l2_doc = None
    if selected_l2 and selected_l2 != "— Selecione —":
        l2_doc = next((c for c in l2_options if c["name"] == selected_l2), None)

    # Monta o campo category
    if l2_doc:
        full_path = l2_doc.get("full_path") or f"{selected_l1} > {selected_l2}"
        category = {
            "level1_id": l1_doc["_id"] if l1_doc else None,
            "level1_name": selected_l1,
            "level2_id": l2_doc["_id"],
            "level2_name": selected_l2,
            "full_path": full_path,
        }
    else:
        full_path = selected_l1
        category = {
            "level1_id": l1_doc["_id"] if l1_doc else None,
            "level1_name": selected_l1,
            "level2_id": None,
            "level2_name": None,
            "full_path": full_path,
        }

    try:
        # Atualiza a transação
        ok = transaction_repo.update_category(tx_id, category, method="manual")
        if not ok:
            st.error("Não foi possível salvar a categoria.")
            return

        # Salva regra automática
        desc_norm = normalize_description(tx.get("description", ""))
        if desc_norm:
            rule_repo.upsert_from_confirmation(desc_norm, category)

        st.success(f"✅ Categorizado como: **{full_path}**")
        st.rerun()

    except Exception as exc:
        st.error(f"Erro ao confirmar categoria: {exc}")
        logger.error("Erro em _handle_confirm (%s): %s", tx_id, exc, exc_info=True)


def _handle_ignore(tx_id: str) -> None:
    """Marca uma transação como ignorada."""
    try:
        transaction_repo.update_ignored(tx_id, ignored=True)
        st.info("Transação ignorada.")
        st.rerun()
    except Exception as exc:
        st.error(f"Erro ao ignorar transação: {exc}")


# ── Utilitários ──────────────────────────────────────────────────────────────

def _build_category_map(tree: list[dict]) -> dict[str, list[dict]]:
    """
    Converte a árvore de categorias em um mapa {level1_name: [l2_docs]}.

    Args:
        tree: Lista retornada por category_repo.build_tree().
    """
    return {cat["name"]: cat.get("children", []) for cat in tree}


def _default_level1(
    llm_suggestion: str | None,
    level1_names: list[str],
) -> str | None:
    """
    Tenta inferir o nível 1 padrão a partir da sugestão da LLM.

    Exemplo: sugestão "Alimentação > Delivery" → level1 = "Alimentação"
    """
    if not llm_suggestion:
        return None
    parts = llm_suggestion.split(">")
    candidate = parts[0].strip() if parts else None
    if candidate in level1_names:
        return candidate
    return None


def _confidence_label(confidence: float | None) -> str:
    """Converte confiança (0–1) para label com emoji."""
    if confidence is None:
        return ""
    if confidence >= 0.8:
        return "🟢 alta"
    if confidence >= 0.5:
        return "🟡 média"
    return "🔴 baixa"
