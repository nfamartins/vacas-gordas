"""Entrypoint principal do Vacas Gordas.

Execute com:
    streamlit run app.py
"""
from __future__ import annotations

import logging

import streamlit as st

# ── Configuração da página (deve ser a primeira chamada Streamlit) ─────────────
st.set_page_config(
    page_title="Vacas Gordas 🐄",
    page_icon="🐄",
    layout="wide",
    initial_sidebar_state="expanded",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Inicialização do banco de dados ───────────────────────────────────────────

@st.cache_resource(show_spinner="Conectando ao MongoDB…")
def _init_db():
    """
    Inicializa a conexão com o MongoDB uma única vez por processo.

    st.cache_resource garante que a conexão não seja recriada a cada
    reexecução do script pelo Streamlit.

    Returns:
        True se a conexão foi bem-sucedida.

    Raises:
        ConnectionError: se o MongoDB não estiver acessível.
    """
    from database.connection import get_db
    get_db()   # lança ConnectionError se falhar
    return True


# ── Inicializa (ou recupera do cache) a conexão ───────────────────────────────
try:
    _init_db()
except Exception as exc:
    st.error(
        f"**❌ Erro ao conectar ao MongoDB**\n\n"
        f"{exc}\n\n"
        "Verifique se o serviço `mongod` está em execução e recarregue a página."
    )
    st.stop()


# ── Alerta de API não configurada ─────────────────────────────────────────────
from config.settings import settings  # noqa: E402 — após st.set_page_config

if not settings.anthropic_configured:
    st.warning(
        "⚠️ **ANTHROPIC_API_KEY não configurada.** "
        "A categorização por IA e o chat financeiro não estarão disponíveis. "
        "Adicione a chave no arquivo `.env` e reinicie a aplicação.",
        icon="⚠️",
    )


# ── Roteamento de páginas ─────────────────────────────────────────────────────
from ui.components.sidebar import render_sidebar  # noqa: E402
from ui.pages import (  # noqa: E402
    render_categorization,
    render_chat,
    render_dashboard,
    render_import,
    render_transactions,
)

_PAGES: dict[str, callable] = {
    "Dashboard": render_dashboard,
    "Importar": render_import,
    "Categorizar": render_categorization,
    "Transações": render_transactions,
    "Chat Financeiro": render_chat,
}

current_page = render_sidebar()

render_fn = _PAGES.get(current_page)
if render_fn:
    try:
        render_fn()
    except Exception as exc:
        logger.error("Erro ao renderizar página '%s': %s", current_page, exc, exc_info=True)
        st.error(
            f"**Ocorreu um erro inesperado na página '{current_page}'.**\n\n"
            f"`{exc}`\n\nVerifique o console para mais detalhes."
        )
else:
    st.error(f"Página '{current_page}' não encontrada.")
