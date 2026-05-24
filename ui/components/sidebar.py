"""Menu lateral de navegação do Vacas Gordas."""
from __future__ import annotations

import streamlit as st

_PAGES = [
    "Dashboard",
    "Importar",
    "Categorizar",
    "Transações",
    "Chat Financeiro",
]

_VERSION = "v0.1.0"


def _get_pending_count() -> int:
    """Retorna a contagem de transações pendentes de categorização."""
    try:
        from database.repositories.transaction_repo import transaction_repo
        return transaction_repo.count_pending()
    except Exception:
        return 0


def render_sidebar() -> str:
    """
    Renderiza o menu lateral e retorna o nome da página selecionada.

    Returns:
        Nome da página ativa (ex: "Dashboard", "Importar").
    """
    with st.sidebar:
        st.title("🐄 Vacas Gordas")
        st.divider()

        pending = _get_pending_count()

        # Monta labels com badge de pendentes na página Categorizar
        labels = []
        for page in _PAGES:
            if page == "Categorizar" and pending > 0:
                labels.append(f"Categorizar ({pending})")
            else:
                labels.append(page)

        selected_label = st.radio(
            "Navegação",
            labels,
            label_visibility="collapsed",
        )

        # Resolve o nome da página sem o badge
        selected_page = selected_label.split(" (")[0]

        st.divider()

        # Rodapé com versão
        st.caption(f"🐄 Vacas Gordas  {_VERSION}")

    return selected_page
