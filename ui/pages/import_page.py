"""Página de importação de extratos e faturas bancárias."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

import streamlit as st
from bson import ObjectId

from decimal import Decimal

from database.repositories.account_repo import account_repo
from database.repositories.import_repo import import_repo
from database.repositories.transaction_repo import transaction_repo
from modules.importer import detect_parser, import_file, import_file_generic
from modules.importer.banks.generic import ColumnMapping, GenericCsvParser
from modules.importer.normalizer import compute_dedup_key, normalize_description

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

    # ── Fatura de cartão: solicita data de vencimento ─────────────────────────
    is_fatura = parser_class.parser_id == "c6_csv" and "fatura" in filename.lower()
    payment_date_str: str | None = None

    if is_fatura:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
        default_date = (
            datetime.strptime(m.group(1), "%Y-%m-%d").date() if m
            else datetime.today().date()
        )
        st.info(
            "💳 **Fatura de cartão detectada.** Informe a data de vencimento — "
            "ela será usada como data dos lançamentos no dashboard."
        )
        payment_date = st.date_input(
            "Data de vencimento da fatura",
            value=default_date,
            key="fatura_payment_date",
            help="Data em que a fatura foi/será paga. Todos os lançamentos usarão esta data no dashboard.",
        )
        payment_date_str = payment_date.strftime("%Y-%m-%d")

    # ── Importa (ou usa cache) ─────────────────────────────────────────────────
    # Chave inclui payment_date para re-importar quando a data mudar
    cache_key = f"{filename}_{payment_date_str or ''}"
    if st.session_state.get("import_result_key") != cache_key:
        with st.spinner("Extraindo transações…"):
            try:
                result = import_file(
                    filename, raw_content,
                    account_id_str=account_id,
                    import_id_str=str(ObjectId()),
                    parser_class=parser_class,
                    payment_date=payment_date_str,
                )
                st.session_state["import_result"] = result
                st.session_state["import_result_key"] = cache_key
                st.session_state["import_filename"] = filename
                st.session_state["import_raw"] = raw_content
                st.session_state["import_hash"] = file_hash
                # Força re-anotação e limpa overrides de transferência
                st.session_state.pop("import_annotated_for", None)
                st.session_state.pop("transfer_overrides", None)
            except Exception as exc:
                st.error(f"Erro ao processar arquivo: {exc}")
                return

    result = st.session_state.get("import_result", {})
    transactions = result.get("transactions", [])

    # Anota duplicatas (uma vez por resultado)
    if st.session_state.get("import_annotated_for") != cache_key:
        _annotate_duplicates(transactions)
        st.session_state["import_annotated_for"] = cache_key

    _render_import_preview(transactions, is_fatura=is_fatura)
    _render_transfer_section(transactions)
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


def _annotate_duplicates(transactions: list[dict]) -> None:
    """
    Anota cada transação com '_dup_status' (in-place):

    - "nova"               → não é duplicata
    - "duplicata_interna"  → dedup_key aparece mais de uma vez dentro deste arquivo
    - "ja_importada"       → dedup_key já existe no banco de dados
    """
    dedup_keys = [tx["dedup_key"] for tx in transactions]
    existing_in_db = transaction_repo.find_existing_dedup_keys(dedup_keys)

    seen: set[str] = set()
    for tx in transactions:
        key = tx["dedup_key"]
        if key in existing_in_db:
            tx["_dup_status"] = "ja_importada"
        elif key in seen:
            tx["_dup_status"] = "duplicata_interna"
        else:
            tx["_dup_status"] = "nova"
            seen.add(key)


def _render_import_preview(transactions: list[dict], is_fatura: bool = False) -> None:
    """Exibe preview das transações extraídas antes da confirmação."""
    if not transactions:
        st.warning("Nenhuma transação foi extraída do arquivo.")
        return

    n_nova = sum(1 for tx in transactions if tx.get("_dup_status") == "nova")
    n_db   = sum(1 for tx in transactions if tx.get("_dup_status") == "ja_importada")
    n_int  = sum(1 for tx in transactions if tx.get("_dup_status") == "duplicata_interna")

    st.subheader(f"Preview — {len(transactions)} transação(ões) encontrada(s)")

    c1, c2, c3 = st.columns(3)
    c1.metric("✅ Novas", n_nova)
    c2.metric("⚠️ Já importadas", n_db,
              help="Mesmo lançamento existe em importação anterior")
    c3.metric("🔄 Duplicatas internas", n_int,
              help="Mesmo lançamento aparece mais de uma vez neste arquivo")

    preview = transactions[:50]
    has_installment = any(tx.get("installment") for tx in preview)

    _STATUS_LABEL = {
        "nova":              "✅ Nova",
        "ja_importada":      "⚠️ Já importada",
        "duplicata_interna": "🔄 Dup. interna",
    }

    rows = []
    for tx in preview:
        row: dict = {"Status": _STATUS_LABEL.get(tx.get("_dup_status", "nova"), "")}

        if is_fatura:
            # Fatura: mostra data de compra + data de pagamento separadas
            row["Data Compra"] = tx.get("purchase_date") or tx.get("date", "")
            row["Data Pgto"]   = tx.get("date", "")
        else:
            row["Data"] = tx.get("date", "")

        row["Descrição"] = tx.get("raw", {}).get("original_description", "")[:60]
        row["Valor"] = f"R$ {abs(float(tx.get('amount', 0))):,.2f}"
        row["Tipo"]  = "Débito" if tx.get("type") == "debit" else "Crédito"

        if has_installment:
            row["Parcela"] = tx.get("installment") or "—"

        rows.append(row)

    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if len(transactions) > 50:
        st.caption(f"… e mais {len(transactions) - 50} transação(ões) não exibidas.")


def _render_transfer_section(transactions: list[dict]) -> None:
    """
    Exibe lançamentos auto-detectados como transferência/pagamento de fatura.

    Permite confirmar ou rejeitar a classificação antes de importar.
    Os overrides ficam em st.session_state["transfer_overrides"].
    """
    auto_transfers = [tx for tx in transactions if tx.get("is_transfer")]
    if not auto_transfers:
        return

    st.divider()
    with st.expander(
        f"💳 {len(auto_transfers)} pagamento(s) de fatura detectado(s) automaticamente",
        expanded=True,
    ):
        st.caption(
            "Lançamentos classificados como transferência **não entram** no cálculo de despesas "
            "do dashboard. Desmarque caso queira contabilizá-los como despesa."
        )

        if "transfer_overrides" not in st.session_state:
            st.session_state["transfer_overrides"] = {}

        import pandas as pd
        h = st.columns([0.5, 1.2, 4.5, 1.8, 1.5])
        for col, lbl in zip(h, ["Transf.", "Data", "Descrição", "Valor", "Conta vinculada"]):
            col.markdown(f"**{lbl}**")

        for tx in auto_transfers:
            key = tx["dedup_key"]
            current = st.session_state["transfer_overrides"].get(key, True)
            cols = st.columns([0.5, 1.2, 4.5, 1.8, 1.5])
            new_val = cols[0].checkbox(
                "", value=current, key=f"tr_{key}", label_visibility="collapsed"
            )
            if new_val != current:
                st.session_state["transfer_overrides"][key] = new_val
            cols[1].write(tx.get("date", ""))
            cols[2].write(tx.get("raw", {}).get("original_description", "")[:65])
            cols[3].write(f"R$ {abs(float(tx.get('amount', 0))):,.2f}")
            # Placeholder para futura seleção de conta vinculada
            cols[4].caption("— (a implementar)")


def _disambiguate_duplicates(transactions: list[dict]) -> list[dict]:
    """
    Prepara as transações para inserção:
    - Remove o campo temporário _dup_status.
    - Para duplicatas mantidas (não excluídas), adiciona sufixo numérico à
      descrição (" (2)", " (3)"...) e recomputa description_normalized e
      dedup_key — evitando rejeição silenciosa pelo índice único do MongoDB.
    """
    key_suffix: dict[str, int] = {}  # dedup_key original → próximo sufixo
    result = []

    for tx in transactions:
        status = tx.get("_dup_status", "nova")
        doc = {k: v for k, v in tx.items() if k != "_dup_status"}

        if status in ("ja_importada", "duplicata_interna"):
            orig_key = tx["dedup_key"]
            key_suffix[orig_key] = key_suffix.get(orig_key, 1) + 1
            n = key_suffix[orig_key]
            suffix = f" ({n})"

            doc["description"] = doc["description"] + suffix
            if doc.get("raw"):
                doc["raw"] = {
                    **doc["raw"],
                    "original_description": doc["raw"].get("original_description", "") + suffix,
                }
            new_norm = normalize_description(doc["description"])
            doc["description_normalized"] = new_norm
            doc["dedup_key"] = compute_dedup_key(
                doc["account_id"],
                doc["date"],
                new_norm,
                Decimal(str(abs(float(doc["amount"])))),
            )

        result.append(doc)

    return result


def _render_confirm_button(
    transactions: list[dict],
    account_id: str,
    account_name: str,
    filename: str,
    file_hash: str,
    parser_id: str,
    institution: str,
) -> None:
    """Seção de filtros de duplicatas, detalhes e botão de confirmação."""
    if not transactions:
        return

    dups_db  = [tx for tx in transactions if tx.get("_dup_status") == "ja_importada"]
    dups_int = [tx for tx in transactions if tx.get("_dup_status") == "duplicata_interna"]
    n_db, n_int = len(dups_db), len(dups_int)

    st.divider()

    excl_db = excl_int = False
    if n_db > 0 or n_int > 0:
        st.markdown("**Gerenciar duplicatas antes de importar:**")

        # Expander com detalhes das duplicatas
        with st.expander(f"Ver detalhes das {n_db + n_int} duplicata(s) encontrada(s)"):
            import pandas as pd

            if dups_db:
                st.markdown(f"**⚠️ {n_db} já importada(s) anteriormente** — existem no banco de dados:")
                st.dataframe(
                    pd.DataFrame([{
                        "Data": tx.get("date", ""),
                        "Descrição": tx.get("raw", {}).get("original_description", "")[:60],
                        "Valor": f"R$ {abs(float(tx.get('amount', 0))):,.2f}",
                        "Tipo": "Débito" if tx.get("type") == "debit" else "Crédito",
                    } for tx in dups_db]),
                    use_container_width=True, hide_index=True,
                )

            if dups_int:
                st.markdown(f"**🔄 {n_int} duplicata(s) interna(s)** — aparece mais de uma vez neste arquivo:")
                st.dataframe(
                    pd.DataFrame([{
                        "Data": tx.get("date", ""),
                        "Descrição": tx.get("raw", {}).get("original_description", "")[:60],
                        "Valor": f"R$ {abs(float(tx.get('amount', 0))):,.2f}",
                        "Tipo": "Débito" if tx.get("type") == "debit" else "Crédito",
                    } for tx in dups_int]),
                    use_container_width=True, hide_index=True,
                )

        col_a, col_b = st.columns(2)
        excl_db = col_a.checkbox(
            f"Excluir {n_db} já importada(s) anteriormente",
            value=False, key="excl_db", disabled=(n_db == 0),
        )
        excl_int = col_b.checkbox(
            f"Excluir {n_int} duplicata(s) interna(s) ao arquivo",
            value=False, key="excl_int", disabled=(n_int == 0),
        )
        if not excl_db and n_db:
            col_a.caption("Serão importadas com sufixo *(2)* na descrição.")
        if not excl_int and n_int:
            col_b.caption("Serão importadas com sufixo *(2)* na descrição.")

    to_insert = [
        tx for tx in transactions
        if not (excl_db  and tx.get("_dup_status") == "ja_importada")
        and not (excl_int and tx.get("_dup_status") == "duplicata_interna")
    ]

    st.caption(f"Serão importadas **{len(to_insert)}** transação(ões).")

    if st.button("✅ Confirmar Importação", type="primary", use_container_width=True):
        # Aplica overrides de is_transfer definidos pelo usuário
        overrides = st.session_state.get("transfer_overrides", {})
        for tx in to_insert:
            k = tx.get("dedup_key")
            if k in overrides:
                tx["is_transfer"] = overrides[k]

        # Desambigua duplicatas mantidas e remove campo temporário
        clean = _disambiguate_duplicates(to_insert)
        _execute_import(
            clean, account_id, account_name,
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
    for key in ("import_result", "import_result_key", "import_filename", "import_raw",
                "import_hash", "import_annotated_for", "transfer_overrides"):
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
    """Exibe o histórico das últimas importações com opção de exclusão."""
    st.subheader("Últimas Importações")

    try:
        imports = import_repo.find_recent(limit=20)
    except Exception as exc:
        st.error(f"Erro ao carregar histórico: {exc}")
        return

    if not imports:
        st.info("Nenhuma importação registrada ainda.")
        return

    # Cabeçalho das colunas
    h = st.columns([2.5, 1.2, 0.8, 0.8, 0.8, 1.5, 0.5])
    for col, label in zip(h, ["Arquivo", "Parser", "Status", "Inseridas", "Duplicatas", "Data", ""]):
        col.markdown(f"**{label}**")
    st.divider()

    for imp in imports:
        imp_id = imp.get("_id", "")
        stats  = imp.get("stats", {})
        filename_imp = imp.get("filename", "—")
        inserted = stats.get("inserted", "—")

        cols = st.columns([2.5, 1.2, 0.8, 0.8, 0.8, 1.5, 0.5])
        cols[0].write(filename_imp)
        cols[1].write(imp.get("parser_used", "—"))
        cols[2].write(imp.get("status", "—"))
        cols[3].write(inserted)
        cols[4].write(stats.get("duplicates_skipped", "—"))
        cols[5].write(_fmt_datetime(imp.get("imported_at")))

        with cols[6]:
            with st.popover("🗑️", help="Apagar importação e suas transações"):
                n_tx = inserted if isinstance(inserted, int) else "?"
                st.warning(
                    f"Apagar **{filename_imp}** e suas **{n_tx}** transação(ões)?  \n"
                    "Esta ação é irreversível."
                )
                if st.button("Confirmar exclusão", key=f"del_{imp_id}", type="primary"):
                    try:
                        deleted_tx = transaction_repo.delete_by_import_id(imp_id)
                        import_repo.delete(imp_id)
                        st.success(f"✅ {deleted_tx} transação(ões) removida(s).")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Erro ao apagar: {exc}")


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
