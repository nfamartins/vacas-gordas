"""Página do Chat Financeiro — interface conversacional com a LLM.

A integração com a LLM (modules/chat/) ainda não foi implementada.
Por enquanto exibe a interface completa com um placeholder que informa ao usuário.

Estrutura preparada para receber:
    from modules.chat.chat_engine import chat_engine
    response = chat_engine.chat(session_id, user_message)
"""
from __future__ import annotations

import logging
from datetime import datetime

import streamlit as st

from database.repositories.chat_repo import chat_repo

logger = logging.getLogger(__name__)

_PLACEHOLDER_RESPONSE = (
    "🚧 **Chat financeiro em implementação.**\n\n"
    "O módulo `modules/chat/chat_engine.py` está sendo desenvolvido. "
    "Em breve você poderá perguntar coisas como:\n\n"
    "- *'Quanto gastei com delivery em maio?'*\n"
    "- *'Qual foi meu maior gasto no trimestre?'*\n"
    "- *'Compare meus gastos de março com abril.'*\n\n"
    "Volte em breve! 🐄"
)

_WELCOME_MESSAGE = (
    "👋 Olá! Sou o assistente financeiro do **Vacas Gordas**.\n\n"
    "Posso ajudar a analisar seus gastos, identificar padrões e "
    "responder perguntas sobre suas finanças.\n\n"
    "*Funcionalidade em implementação — aguarde!*"
)


def render() -> None:
    """Renderiza a página do chat financeiro."""
    st.title("💬 Chat Financeiro")

    # Inicializa sessão de chat no session_state
    if "chat_session_id" not in st.session_state:
        st.session_state["chat_session_id"] = None
    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []

    # Barra de ações
    col_info, col_clear = st.columns([5, 1])
    with col_info:
        st.caption(
            "💡 Pergunte sobre seus gastos, categorias, períodos ou tendências financeiras."
        )
    with col_clear:
        if st.button("🗑️ Limpar", help="Limpar conversa atual"):
            _clear_conversation()
            st.rerun()

    st.divider()

    # Exibe histórico de mensagens
    messages = st.session_state.get("chat_messages", [])

    if not messages:
        # Mensagem de boas-vindas
        with st.chat_message("assistant", avatar="🐄"):
            st.markdown(_WELCOME_MESSAGE)
    else:
        for msg in messages:
            role = msg["role"]
            avatar = "🐄" if role == "assistant" else "👤"
            with st.chat_message(role, avatar=avatar):
                st.markdown(msg["content"])

    # Input do usuário
    user_input = st.chat_input("Pergunte sobre suas finanças…")

    if user_input:
        _handle_user_message(user_input)
        st.rerun()


# ── Handlers ──────────────────────────────────────────────────────────────────

def _handle_user_message(user_input: str) -> None:
    """
    Processa a mensagem do usuário e gera uma resposta.

    Quando o chat_engine estiver implementado, substituir o placeholder
    pela chamada real:
        response = chat_engine.chat(session_id, user_input)
    """
    # Garante que a sessão existe
    if not st.session_state.get("chat_session_id"):
        try:
            st.session_state["chat_session_id"] = chat_repo.create_session()
        except Exception as exc:
            logger.warning("Não foi possível criar sessão de chat no banco: %s", exc)
            st.session_state["chat_session_id"] = "local"

    session_id = st.session_state["chat_session_id"]

    # Adiciona mensagem do usuário ao histórico local
    user_msg = {
        "role": "user",
        "content": user_input,
        "timestamp": datetime.now().isoformat(),
    }
    st.session_state["chat_messages"].append(user_msg)

    # Persiste no banco (best-effort)
    if session_id != "local":
        try:
            chat_repo.append_message(session_id, "user", user_input)
        except Exception as exc:
            logger.warning("Erro ao persistir mensagem do usuário: %s", exc)

    # ── Geração da resposta ────────────────────────────────────────────────────
    # TODO: substituir pelo chat_engine real quando implementado
    # try:
    #     from modules.chat.chat_engine import chat_engine
    #     response_text = chat_engine.chat(session_id, user_input)
    # except Exception as exc:
    #     response_text = f"Erro ao processar resposta: {exc}"

    response_text = _generate_placeholder_response(user_input)

    # Adiciona resposta ao histórico local
    assistant_msg = {
        "role": "assistant",
        "content": response_text,
        "timestamp": datetime.now().isoformat(),
    }
    st.session_state["chat_messages"].append(assistant_msg)

    # Persiste resposta no banco (best-effort)
    if session_id != "local":
        try:
            chat_repo.append_message(session_id, "assistant", response_text)
        except Exception as exc:
            logger.warning("Erro ao persistir resposta do assistente: %s", exc)


def _generate_placeholder_response(user_input: str) -> str:
    """
    Gera resposta placeholder enquanto o chat_engine não está implementado.

    Reconhece algumas perguntas comuns para dar respostas mais úteis.
    """
    lower = user_input.lower()

    if any(w in lower for w in ["quanto gastei", "total gasto", "gasto com"]):
        return (
            "📊 Para responder sobre seus gastos, preciso do acesso ao chat engine "
            "com contexto financeiro. **Implementação em breve!**\n\n"
            + _PLACEHOLDER_RESPONSE
        )

    if any(w in lower for w in ["categoria", "classificar", "categorizar"]):
        return (
            "🏷️ Boa pergunta! Quando o chat estiver ativo, poderei ajudar com "
            "análises por categoria detalhadas.\n\n"
            + _PLACEHOLDER_RESPONSE
        )

    return _PLACEHOLDER_RESPONSE


def _clear_conversation() -> None:
    """Limpa o histórico local da conversa."""
    st.session_state["chat_messages"] = []
    st.session_state["chat_session_id"] = None
