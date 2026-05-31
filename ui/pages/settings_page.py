"""Página de Configurações — gerenciamento do banco de dados e status do sistema."""
from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)


def render() -> None:
    """Renderiza a página de Configurações."""
    st.title("⚙️ Configurações")

    tab_db, tab_config = st.tabs(["🗄️ Banco de Dados", "🔧 Configurações Atuais"])

    with tab_db:
        _render_database_tab()

    with tab_config:
        _render_config_tab()


# ── Aba: Banco de Dados ───────────────────────────────────────────────────────

def _render_database_tab() -> None:
    """Status da conexão, coleções e ações de init/reset."""
    from database.connection import MongoConnection
    from database.setup import get_status, run_full_init

    # ── Status da conexão ─────────────────────────────────────────────────────
    st.subheader("Status da Conexão")

    try:
        db = MongoConnection.get_db()
        db.command("ping")
        st.success(f"✅ Conectado — banco: **{db.name}**")
        connection_ok = True
    except Exception as exc:
        st.error(f"❌ Sem conexão: {exc}")
        connection_ok = False

    if not connection_ok:
        return

    # ── Status das coleções ───────────────────────────────────────────────────
    st.subheader("Coleções")

    try:
        status = get_status(db)
        _render_collections_table(status["collections"])
        st.caption(f"Total de documentos: **{status['total_docs']:,}**")
    except Exception as exc:
        st.error(f"Erro ao carregar status: {exc}")

    st.divider()

    # ── Ações ─────────────────────────────────────────────────────────────────
    st.subheader("Ações")

    col_init, col_reset = st.columns([1, 1])

    # Botão: Inicializar / Atualizar
    with col_init:
        st.markdown(
            "**▶ Inicializar / Atualizar Banco**  \n"
            "Cria coleções e índices ausentes. Insere categorias padrão.  \n"
            "Operação idempotente — segura de executar múltiplas vezes."
        )
        if st.button("▶ Inicializar Banco", type="primary", use_container_width=True):
            _run_init(db, run_full_init, reset=False)

    # Botão: Reset
    with col_reset:
        st.markdown(
            "**⚠️ Resetar Banco**  \n"
            "Apaga **todos** os dados e reinicializa do zero.  \n"
            "Esta operação é **irreversível**."
        )

        confirm = st.checkbox(
            "Confirmo que quero apagar todos os dados",
            key="confirm_reset",
        )
        reset_btn = st.button(
            "⚠️ Resetar Banco",
            type="secondary",
            use_container_width=True,
            disabled=not confirm,
        )
        if reset_btn and confirm:
            _run_init(db, run_full_init, reset=True)


def _run_init(db, run_full_init_fn, reset: bool) -> None:
    """Executa init/reset com feedback visual."""
    label = "Resetando e inicializando…" if reset else "Inicializando banco…"
    with st.spinner(label):
        try:
            result = run_full_init_fn(db, reset=reset)
        except Exception as exc:
            st.error(f"Erro durante a inicialização: {exc}")
            return

    # Relatório
    if reset:
        st.success("🔄 Banco resetado e reinicializado com sucesso!")
    else:
        st.success("✅ Banco inicializado / atualizado com sucesso!")

    col_a, col_b, col_c = st.columns(3)
    created = result.get("collections_created", [])
    col_a.metric("Coleções criadas", len(created))
    col_b.metric("Categorias inseridas", result.get("categories_inserted", 0))
    col_c.metric("Erros", len(result.get("errors", [])))

    if created:
        st.info(f"Novas coleções: {', '.join(created)}")

    if result.get("errors"):
        with st.expander("⚠️ Erros não-fatais"):
            for err in result["errors"]:
                st.warning(err)

    # Invalida o cache de conexão para forçar atualização do status na sidebar
    st.cache_resource.clear()
    st.rerun()


def _render_collections_table(collections: list[dict]) -> None:
    """Tabela com status de cada coleção."""
    import pandas as pd

    rows = []
    for col in collections:
        doc_count = col["doc_count"]
        index_count = col["index_count"]
        rows.append({
            "Coleção": col["name"],
            "Documentos": f"{doc_count:,}" if doc_count >= 0 else "—",
            "Índices": index_count if index_count >= 0 else "—",
            "Status": "✅" if doc_count >= 0 else "⚠️",
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Status": st.column_config.TextColumn("", width="small"),
        },
    )


# ── Aba: Configurações Atuais ─────────────────────────────────────────────────

def _render_config_tab() -> None:
    """Exibe as configurações ativas da aplicação (API key mascarada)."""
    from config.settings import settings

    st.subheader("Configurações Ativas")
    st.caption("Lidas do arquivo `.env`. Reinicie a aplicação após alterações.")

    # MongoDB
    st.markdown("**MongoDB**")
    col1, col2 = st.columns(2)
    col1.text_input("URI", value=settings.mongo_uri, disabled=True)
    col2.text_input("Banco", value=settings.mongo_db, disabled=True)

    st.divider()

    # Anthropic
    st.markdown("**Anthropic API**")
    col3, col4, col5 = st.columns(3)

    api_key_display = (
        settings.anthropic_api_key[:8] + "***"
        if settings.anthropic_api_key else "⚠️ Não configurada"
    )
    col3.text_input("API Key", value=api_key_display, disabled=True)
    col4.text_input(
        "Modelo Categorizador",
        value=settings.anthropic_model_categorizer,
        disabled=True,
    )
    col5.text_input(
        "Modelo Chat",
        value=settings.anthropic_model_chat,
        disabled=True,
    )

    if not settings.anthropic_configured:
        st.warning(
            "A ANTHROPIC_API_KEY não está configurada. "
            "Adicione-a ao arquivo `.env` para habilitar categorização por IA e chat."
        )

    st.divider()

    # Aplicação
    st.markdown("**Aplicação**")
    col6, col7 = st.columns(2)
    col6.text_input("Ambiente", value=settings.app_env, disabled=True)
    col7.text_input(
        "Debug",
        value="Ativado" if settings.app_debug else "Desativado",
        disabled=True,
    )

    st.divider()
    st.caption(
        "Para alterar as configurações, edite o arquivo `.env` "
        "na raiz do projeto e reinicie a aplicação."
    )
