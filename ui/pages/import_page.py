"""Página de importação de extratos e faturas bancárias."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import streamlit as st
from bson import ObjectId

from database.repositories.account_repo import account_repo
from database.repositories.import_repo import import_repo
from database.repositories.transaction_repo import transaction_repo
from modules.importer import detect_parser, import_file, import_file_generic
from modules.importer.banks.generic import ColumnMapping, GenericCsvParser

logger = logging.getLogger(__name__)

_FILE_TYPES = ["csv", "pdf", "ofx", "qfx"]


def render() -> None:
    """Renderiza a página de importação de arquivos financeiros."""
    st.title("📥 Importar Arquivos")

    tab_import, tab_history = st.tabs(["Nova Importação", "Histórico"])

    with tab_import:
        _render_import_form()

    with tab_history:
        _render_history()


# ── Formulário de importação ──────────────────────────────────────────────────

def _render_import_form() -> None:
    """Formulário de upload e importação."""
    st.subheader("Upload de Arquivo")

    # Seletor de conta
    account_id, account_name = _render_account_selector()
    if not account_id:
        st.warning("Crie ou selecione uma conta para continuar.")
        return

    # Upload do arquivo
    uploaded = st.file_uploader(
        "Selecione um extrato ou fatura",
        type=_FILE_TYPES,
        help="Formatos aceitos: CSV, PDF, OFX, QFX",
    )

    if not uploaded:
        st.info("📂 Faça o upload de um arquivo para visualizar o preview.")
        return

    raw_content = uploaded.read()
    filename = uploaded.name

    # Verifica se o arquivo já foi importado
    file_hash = "sha256:" + hashlib.sha256(raw_content).hexdigest()
    existing = import_repo.find_by_hash(file_hash)
    if existing:
        st.warning(
            f"⚠️ Este arquivo já foi importado em "
            f"{_fmt_datetime(existing.get('imported_at'))}. "
            "Se deseja reimportar, clique em 'Confirmar mesmo assim'."
        )
        if not st.checkbox("Confirmar reimportação"):
            return

    # Detecta o parser
    parser_class = detect_parser(filename, raw_content)

    if parser_class is None:
        st.info(
            "🔍 Nenhum parser automático reconheceu este arquivo. "
            "Configure o mapeamento de colunas abaixo."
        )
        _render_generic_import(filename, raw_content, account_id, file_hash)
        return

    st.success(
        f"✅ Parser identificado: **{parser_class.institution}** "
        f"(`{parser_class.parser_id}`)"
    )

    # Preview
    if "import_result" not in st.session_state or \
            st.session_state.get("import_filename") != filename:
        with st.spinner("Extraindo transações…"):
            try:
                result = import_file(
                    filename, raw_content,
                    account_id_str=account_id,
                    import_id_str=str(ObjectId()),  # placeholder
                    parser_class=parser_class,
                )
                st.session_state["import_result"] = result
                st.session_state["import_filename"] = filename
                st.session_state["import_raw"] = raw_content
                st.session_state["import_hash"] = file_hash
            except Exception as exc:
                st.error(f"Erro ao processar arquivo: {exc}")
                return

    result = st.session_state.get("import_result", {})
    transactions = result.get("transactions", [])

    _render_import_preview(transactions)
    _render_confirm_button(
        transactions, account_id, account_name, filename, file_hash,
        result.get("parser_id", ""), result.get("institution", ""),
    )


def _render_account_selector() -> tuple[str | None, str]:
    """
    Renderiza seletor de conta e formulário inline para criar nova conta.

    Returns:
        Tupla (account_id, account_name) ou (None, "") se nenhuma conta selecionada.
    """
    accounts = account_repo.find_active()
    options = {acc["name"]: acc["_id"] for acc in accounts}
    options["+ Criar nova conta"] = None

    selected = st.selectbox(
        "Conta bancária",
        list(options.keys()),
        help="Selecione a conta à qual as transações serão vinculadas.",
    )

    if selected == "+ Criar nova conta":
        with st.expander("Nova conta", expanded=True):
            with st.form("form_nova_conta"):
                nome = st.text_input("Nome da conta", placeholder="Ex: C6 Cartão")
                instituicao = st.text_input("Instituição", placeholder="Ex: C6 Bank")
                tipo = st.selectbox(
                    "Tipo",
                    ["checking", "credit_card", "savings", "investment"],
                    format_func=lambda t: {
                        "checking": "Conta Corrente",
                        "credit_card": "Cartão de Crédito",
                        "savings": "Poupança",
                        "investment": "Investimento",
                    }[t],
                )
                submitted = st.form_submit_button("Criar conta")
                if submitted and nome:
                    try:
                        new_id = account_repo.insert({
                            "name": nome,
                            "institution": instituicao,
                            "type": tipo,
                        })
                        st.success(f"Conta '{nome}' criada!")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Erro ao criar conta: {exc}")
        return None, ""

    account_id = options.get(selected)
    return account_id, selected


def _render_import_preview(transactions: list[dict]) -> None:
    """Exibe preview das transações extraídas antes da confirmação."""
    if not transactions:
        st.warning("Nenhuma transação foi extraída do arquivo.")
        return

    st.subheader(f"Preview — {len(transactions)} transação(ões) encontrada(s)")

    # Mostra apenas as primeiras 20 como preview
    preview = transactions[:20]
    rows = []
    for tx in preview:
        rows.append({
            "Data": tx.get("date", ""),
            "Descrição": tx.get("raw", {}).get("original_description", "")[:50],
            "Valor": f"R$ {abs(float(tx.get('amount', 0))):,.2f}",
            "Tipo": "Débito" if tx.get("type") == "debit" else "Crédito",
        })

    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if len(transactions) > 20:
        st.caption(f"… e mais {len(transactions) - 20} transação(ões).")


def _render_confirm_button(
    transactions: list[dict],
    account_id: str,
    account_name: str,
    filename: str,
    file_hash: str,
    parser_id: str,
    institution: str,
) -> None:
    """Botão de confirmação que persiste as transações no banco."""
    if not transactions:
        return

    st.divider()
    if st.button("✅ Confirmar Importação", type="primary", use_container_width=True):
        _execute_import(
            transactions, account_id, account_name,
            filename, file_hash, parser_id, institution,
        )


def _execute_import(
    transactions: list[dict],
    account_id: str,
    account_name: str,
    filename: str,
    file_hash: str,
    parser_id: str,
    institution: str,
) -> None:
    """Executa a importação: registra no imports e insere as transações."""
    # Cria registro de importação
    import_doc_id = import_repo.insert({
        "account_id": account_id,
        "filename": filename,
        "file_hash": file_hash,
        "file_type": filename.rsplit(".", 1)[-1].lower(),
        "source_type": "statement",
        "parser_used": parser_id,
        "institution": institution,
        "status": "processing",
    })

    # Atualiza os documentos com o import_id real
    real_import_oid = ObjectId(import_doc_id)
    for tx in transactions:
        tx["import_id"] = real_import_oid

    # Insere transações
    with st.spinner("Salvando transações…"):
        try:
            result = transaction_repo.insert_many(transactions)
        except Exception as exc:
            import_repo.update_status(import_doc_id, "failed", {}, errors=[str(exc)])
            st.error(f"Erro ao salvar transações: {exc}")
            return

    inserted = result["inserted"]
    skipped = result["skipped"]

    # Atualiza status da importação
    import_repo.update_status(
        import_doc_id,
        status="completed",
        stats={
            "total_transactions": len(transactions),
            "inserted": inserted,
            "duplicates_skipped": skipped,
            "pending_categorization": inserted,
        },
    )

    # Limpa estado da sessão
    for key in ("import_result", "import_filename", "import_raw", "import_hash"):
        st.session_state.pop(key, None)

    st.success(
        f"🎉 Importação concluída!\n\n"
        f"- **{inserted}** transação(ões) inserida(s)\n"
        f"- **{skipped}** duplicata(s) ignorada(s)\n\n"
        f"Acesse **Categorizar** para revisar as categorias sugeridas."
    )
    st.balloons()


# ── Import genérico ───────────────────────────────────────────────────────────

def _render_generic_import(
    filename: str,
    raw_content: bytes,
    account_id: str,
    file_hash: str,
) -> None:
    """Fluxo de importação para arquivos sem parser automático."""
    parser = GenericCsvParser()
    try:
        preview = parser.preview(filename, raw_content)
    except Exception as exc:
        st.error(f"Não foi possível ler o arquivo: {exc}")
        return

    if not preview.columns:
        st.error("Arquivo sem colunas detectadas. Verifique o formato.")
        return

    st.subheader("Mapeamento de Colunas")
    st.caption(
        f"Arquivo: `{filename}` — {preview.total_rows} linhas, "
        f"encoding `{preview.detected_encoding}`, "
        f"delimitador `{repr(preview.detected_delimiter)}`"
    )

    import pandas as pd
    st.dataframe(
        pd.DataFrame(preview.sample_rows),
        use_container_width=True,
        hide_index=True,
    )

    with st.form("form_mapeamento"):
        col_options = preview.columns
        col1, col2, col3 = st.columns(3)
        date_col = col1.selectbox("Coluna de Data", col_options)
        desc_col = col2.selectbox("Coluna de Descrição", col_options)
        amt_col = col3.selectbox("Coluna de Valor", col_options)

        col4, col5 = st.columns(2)
        date_fmt = col4.text_input("Formato da Data", value="%d/%m/%Y",
                                   help="Ex: %d/%m/%Y para 31/12/2024")
        dec_sep = col5.selectbox("Separador Decimal", [",", "."], index=0)

        submitted = st.form_submit_button("Importar com este mapeamento")

    if submitted:
        mapping = ColumnMapping(
            date_column=date_col,
            description_column=desc_col,
            amount_column=amt_col,
            date_format=date_fmt,
            decimal_separator=dec_sep,
        )
        try:
            result = import_file_generic(
                filename, raw_content,
                account_id_str=account_id,
                import_id_str=str(ObjectId()),
                column_mapping=mapping,
            )
            _execute_import(
                result["transactions"], account_id, "Genérico",
                filename, file_hash, result["parser_id"], result["institution"],
            )
        except Exception as exc:
            st.error(f"Erro na importação genérica: {exc}")


# ── Histórico ─────────────────────────────────────────────────────────────────

def _render_history() -> None:
    """Exibe o histórico das últimas importações."""
    st.subheader("Últimas Importações")

    try:
        imports = import_repo.find_recent(limit=20)
    except Exception as exc:
        st.error(f"Erro ao carregar histórico: {exc}")
        return

    if not imports:
        st.info("Nenhuma importação registrada ainda.")
        return

    import pandas as pd
    rows = []
    for imp in imports:
        stats = imp.get("stats", {})
        rows.append({
            "Arquivo": imp.get("filename", "—"),
            "Parser": imp.get("parser_used", "—"),
            "Status": imp.get("status", "—"),
            "Inseridas": stats.get("inserted", "—"),
            "Duplicatas": stats.get("duplicates_skipped", "—"),
            "Data": _fmt_datetime(imp.get("imported_at")),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ── Utilitários ─���─────────────────────────────────────────────────────────────

def _fmt_datetime(dt) -> str:
    """Formata datetime para exibição."""
    if dt is None:
        return "—"
    try:
        if isinstance(dt, str):
            return dt[:19]
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(dt)
