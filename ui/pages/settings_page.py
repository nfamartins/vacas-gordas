"""Página de Configurações — banco de dados, categorias, regras e settings."""
from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)

# 4 non-breaking spaces por nível de profundidade na árvore de categorias
_CAT_INDENT = " " * 4


def render() -> None:
    """Renderiza a página de Configurações com 4 abas."""
    st.title("⚙️ Configurações")

    tab_db, tab_cats, tab_rules, tab_config = st.tabs([
        "🗄️ Banco de Dados",
        "🏷️ Categorias",
        "📋 Regras de Classificação",
        "🔧 Configurações do Sistema",
    ])

    with tab_db:
        _render_database_tab()

    with tab_cats:
        _render_categories_tab()

    with tab_rules:
        _render_rules_tab()

    with tab_config:
        _render_config_tab()


# ═══════════════════════════════════════════════════════════════════════════════
# ABA: Banco de Dados
# ═══════════════════════════════════════════════════════════════════════════════

def _render_database_tab() -> None:
    from database.connection import MongoConnection
    from database.setup import get_status, run_full_init

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

    st.subheader("Coleções")
    try:
        status = get_status(db)
        _render_collections_table(status["collections"])
        st.caption(f"Total de documentos: **{status['total_docs']:,}**")
    except Exception as exc:
        st.error(f"Erro ao carregar status: {exc}")

    st.divider()
    st.subheader("Ações")

    col_init, col_reset = st.columns(2)
    with col_init:
        st.markdown(
            "**▶ Inicializar / Atualizar**  \n"
            "Cria coleções e índices ausentes. Insere categorias padrão.  \n"
            "Operação idempotente — segura de executar múltiplas vezes."
        )
        if st.button("▶ Inicializar Banco", type="primary", use_container_width=True):
            _run_init(db, run_full_init, reset=False)

    with col_reset:
        st.markdown(
            "**⚠️ Resetar Banco**  \n"
            "Apaga **todos** os dados e reinicializa do zero.  \n"
            "Esta operação é **irreversível**."
        )
        confirm = st.checkbox("Confirmo que quero apagar todos os dados", key="confirm_reset")
        if st.button(
            "⚠️ Resetar Banco", type="secondary",
            use_container_width=True, disabled=not confirm,
        ):
            _run_init(db, run_full_init, reset=True)


def _run_init(db, run_full_init_fn, reset: bool) -> None:
    label = "Resetando e inicializando…" if reset else "Inicializando banco…"
    with st.spinner(label):
        try:
            result = run_full_init_fn(db, reset=reset)
        except Exception as exc:
            st.error(f"Erro durante a inicialização: {exc}")
            return

    st.success("🔄 Banco resetado!" if reset else "✅ Banco inicializado!")
    c1, c2, c3 = st.columns(3)
    c1.metric("Coleções criadas", len(result.get("collections_created", [])))
    c2.metric("Categorias inseridas", result.get("categories_inserted", 0))
    c3.metric("Erros", len(result.get("errors", [])))

    if result.get("errors"):
        with st.expander("⚠️ Erros"):
            for err in result["errors"]:
                st.warning(err)

    st.cache_resource.clear()
    st.rerun()


def _render_collections_table(collections: list[dict]) -> None:
    import pandas as pd
    rows = [{
        "Coleção": c["name"],
        "Documentos": f"{c['doc_count']:,}" if c["doc_count"] >= 0 else "—",
        "Índices": c["index_count"] if c["index_count"] >= 0 else "—",
        "": "✅" if c["doc_count"] >= 0 else "⚠️",
    } for c in collections]
    st.dataframe(
        pd.DataFrame(rows), use_container_width=True, hide_index=True,
        column_config={"": st.column_config.TextColumn("", width="small")},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ABA: Categorias
# ═══════════════════════════════════════════════════════════════════════════════

def _render_categories_tab() -> None:
    """
    Árvore de categorias com dropdowns colapsáveis por nível.

    Cada nó com filhos exibe um botão ▶/▼ para expandir/recolher.
    O estado de expansão é mantido em st.session_state["cat_expanded"].
    """
    from database.connection import MongoConnection
    from database.setup import reset_categories
    from database.repositories.category_repo import category_repo

    st.subheader("Categorias de Gastos")
    st.caption(
        "Use **▶ / ▼** para expandir ou recolher cada nível da hierarquia. "
        "Categorias desativadas não aparecem nos seletores de lançamentos."
    )

    # ── Estado de expansão ────────────────────────────────────────────────────
    if "cat_expanded" not in st.session_state:
        st.session_state["cat_expanded"] = set()

    # ── Barra de ferramentas ──────────────────────────────────────────────────
    c_add, c_exp, c_col, c_inactive, c_reset = st.columns([2.2, 0.8, 0.8, 1.5, 1.5])

    with c_add:
        if st.button("➕ Nova categoria raiz", use_container_width=True):
            st.session_state["adding_root_cat"] = True

    # Carrega a árvore completa (para expandir/recolher tudo)
    try:
        full_tree = category_repo.build_tree(active_only=False)
    except Exception:
        full_tree = []

    if c_exp.button("⊞", help="Expandir tudo", use_container_width=True):
        st.session_state["cat_expanded"] = _collect_all_parent_ids(full_tree)
        st.rerun()

    if c_col.button("⊟", help="Recolher tudo", use_container_width=True):
        st.session_state["cat_expanded"] = set()
        st.rerun()

    show_inactive = c_inactive.toggle("Mostrar inativas", value=False, key="cat_show_inactive")

    with c_reset:
        with st.popover("🔄 Reset Categorias", use_container_width=True):
            st.warning(
                "Apaga **todas** as categorias e recria a árvore padrão.  \n"
                "Transações existentes não são afetadas."
            )
            if st.checkbox("Confirmo o reset", key="confirm_cat_reset"):
                if st.button("Executar reset", type="primary", key="btn_exec_cat_reset"):
                    with st.spinner("Resetando…"):
                        try:
                            db = MongoConnection.get_db()
                            res = reset_categories(db)
                            st.success(f"✅ {res['inserted']} categorias recriadas!")
                            st.session_state["cat_expanded"] = set()
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Erro: {exc}")

    # ── Formulário nova categoria raiz ────────────────────────────────────────
    if st.session_state.get("adding_root_cat"):
        with st.container(border=True):
            _render_new_cat_form(
                category_repo=category_repo,
                parent_id=None,
                parent_path="",
                form_key="form_root_cat",
                cancel_key="adding_root_cat",
            )

    st.divider()

    # ── Cabeçalho das colunas ─────────────────────────────────────────────────
    h = st.columns([0.5, 3.5, 3, 0.5, 0.5, 0.5])
    h[1].markdown("**Categoria**")
    h[2].markdown("**Descrição**")
    st.divider()

    # ── Árvore ────────────────────────────────────────────────────────────────
    try:
        tree = category_repo.build_tree(active_only=not show_inactive)
    except Exception as exc:
        st.error(f"Erro ao carregar categorias: {exc}")
        return

    if not tree:
        st.info(
            "Nenhuma categoria encontrada. "
            "Use **▶ Inicializar Banco** ou **Reset Categorias** para criar a árvore padrão."
        )
        return

    for root_node in tree:
        _render_cat_node(root_node, category_repo, depth=0)


def _render_cat_node(node: dict, category_repo, depth: int) -> None:
    """
    Renderiza um nó da árvore com toggle ▶/▼, indentação e botões de ação.

    Colunas: [▶/▼ | nome indentado | descrição | ✏️ | ➕ | 🚫/✅]

    Os filhos são renderizados recursivamente logo abaixo, apenas se o nó
    estiver em st.session_state["cat_expanded"].
    """
    nid = node["_id"]
    has_children = bool(node.get("children"))
    is_expanded = nid in st.session_state.get("cat_expanded", set())
    is_active = node.get("is_active", True)

    indent = _CAT_INDENT * depth
    inactive_suffix = " *(inativa)*" if not is_active else ""
    name_fmt = f"**{node['name']}**" if has_children else node["name"]

    c_arrow, c_name, c_desc, c_edit, c_add, c_act = st.columns(
        [0.5, 3.5, 3, 0.5, 0.5, 0.5]
    )

    # ── Seta de expansão (apenas para nós com filhos) ─────────────────────────
    if has_children:
        arrow = "▼" if is_expanded else "▶"
        if c_arrow.button(arrow, key=f"exp_{nid}", help="Expandir / Recolher"):
            expanded: set = st.session_state.get("cat_expanded", set()).copy()
            if is_expanded:
                expanded.discard(nid)
            else:
                expanded.add(nid)
            st.session_state["cat_expanded"] = expanded
            st.rerun()

    # ── Nome com indentação ───────────────────────────────────────────────────
    c_name.markdown(f"{indent}{name_fmt}{inactive_suffix}")

    # ── Descrição ─────────────────────────────────────────────────────────────
    desc = node.get("description", "") or ""
    if desc:
        c_desc.caption(desc)

    # ── Editar ────────────────────────────────────────────────────────────────
    if c_edit.button("✏️", key=f"edit_{nid}", help="Editar nome, descrição ou mãe"):
        cur = st.session_state.get("editing_cat_id")
        st.session_state["editing_cat_id"] = None if cur == nid else nid
        st.rerun()

    # ── Adicionar subcategoria ────────────────────────────────────────────────
    add_key = f"adding_child_{nid}"
    if c_add.button("➕", key=f"add_{nid}", help="Adicionar subcategoria"):
        st.session_state[add_key] = True
        # Expande o nó para o formulário ficar visível
        expanded = st.session_state.get("cat_expanded", set()).copy()
        expanded.add(nid)
        st.session_state["cat_expanded"] = expanded
        st.rerun()

    # ── Ativar / Desativar ────────────────────────────────────────────────────
    if is_active:
        if c_act.button("🚫", key=f"deact_{nid}", help="Desativar"):
            category_repo.deactivate(nid)
            st.rerun()
    else:
        if c_act.button("✅", key=f"react_{nid}", help="Reativar"):
            category_repo.reactivate(nid)
            st.rerun()

    # ── Formulário de edição inline ───────────────────────────────────────────
    if st.session_state.get("editing_cat_id") == nid:
        with st.container(border=True):
            _render_edit_form(node, category_repo)

    # ── Formulário de nova subcategoria ───────────────────────────────────────
    if st.session_state.get(add_key):
        with st.container(border=True):
            _render_new_cat_form(
                category_repo=category_repo,
                parent_id=nid,
                parent_path=node["full_path"],
                form_key=f"form_child_{nid}",
                cancel_key=add_key,
            )

    # ── Filhos (renderizados recursivamente se expandido) ─────────────────────
    if is_expanded and has_children:
        for child in node["children"]:
            _render_cat_node(child, category_repo, depth + 1)


def _collect_all_parent_ids(tree: list[dict]) -> set[str]:
    """Coleta os IDs de todos os nós que têm filhos (para 'expandir tudo')."""
    result: set[str] = set()

    def _walk(nodes: list[dict]) -> None:
        for n in nodes:
            if n.get("children"):
                result.add(n["_id"])
                _walk(n["children"])

    _walk(tree)
    return result


def _render_edit_form(node: dict, category_repo) -> None:
    """Formulário inline para editar nome, descrição e categoria mãe."""
    nid = node["_id"]
    st.markdown(f"**Editando: {node['full_path']}**")

    all_cats = category_repo.find_all(active_only=False)
    current_path = node["full_path"]
    valid_parents = [
        c for c in all_cats
        if c["_id"] != nid
        and not c["full_path"].startswith(current_path + " > ")
    ]
    parent_options = ["(sem pai — categoria raiz)"] + [c["full_path"] for c in valid_parents]

    cur_parent_path = " > ".join(current_path.split(" > ")[:-1])
    try:
        cur_idx = next(
            i + 1 for i, c in enumerate(valid_parents)
            if c["full_path"] == cur_parent_path
        )
    except StopIteration:
        cur_idx = 0

    with st.form(f"form_edit_{nid}", clear_on_submit=False):
        new_name = st.text_input("Nome", value=node["name"])
        new_desc = st.text_input("Descrição", value=node.get("description") or "")
        new_parent_label = st.selectbox(
            "Categoria mãe",
            parent_options,
            index=cur_idx,
            help="Mover para outra mãe atualiza automaticamente o caminho completo.",
        )
        c_save, c_cancel = st.columns(2)
        save = c_save.form_submit_button("💾 Salvar", type="primary")
        cancel = c_cancel.form_submit_button("Cancelar")

    if cancel:
        st.session_state.pop("editing_cat_id", None)
        st.rerun()

    if save:
        if not new_name.strip():
            st.warning("O nome não pode ser vazio.")
            return

        update_data: dict = {}
        if new_name.strip() != node["name"]:
            update_data["name"] = new_name.strip()
        if new_desc != (node.get("description") or ""):
            update_data["description"] = new_desc

        if new_parent_label == "(sem pai — categoria raiz)":
            new_parent_id = None
        else:
            new_parent_doc = next(
                (c for c in valid_parents if c["full_path"] == new_parent_label), None
            )
            new_parent_id = new_parent_doc["_id"] if new_parent_doc else None

        cur_parent_id = str(node.get("parent_id") or "")
        if str(new_parent_id or "") != cur_parent_id:
            update_data["parent_id"] = new_parent_id

        if not update_data:
            st.info("Nenhuma alteração detectada.")
            st.session_state.pop("editing_cat_id", None)
            st.rerun()
            return

        try:
            result = category_repo.update(nid, update_data)
            if result["updated"]:
                st.success("✅ Categoria atualizada!")
                if result.get("categories_cascaded", 0) > 0:
                    st.info(f"↻ {result['categories_cascaded']} subcategoria(s) com caminho atualizado.")
            st.session_state.pop("editing_cat_id", None)
            st.rerun()
        except Exception as exc:
            st.error(f"Erro ao atualizar: {exc}")


def _render_new_cat_form(
    category_repo,
    parent_id: str | None,
    parent_path: str,
    form_key: str,
    cancel_key: str,
) -> None:
    """Formulário para criar nova categoria (raiz ou filha)."""
    with st.form(form_key, clear_on_submit=True):
        placeholder = (
            f"Nova subcategoria em '{parent_path.split(' > ')[-1]}'"
            if parent_path else "Nome da nova categoria raiz"
        )
        new_name = st.text_input("Nome", placeholder=placeholder)
        new_desc = st.text_input("Descrição", placeholder="Descrição opcional")

        c_add, c_cancel = st.columns(2)
        add_btn = c_add.form_submit_button("Criar", type="primary")
        cancel_btn = c_cancel.form_submit_button("Cancelar")

    if cancel_btn:
        st.session_state.pop(cancel_key, None)
        st.rerun()

    if add_btn:
        name = new_name.strip()
        if not name:
            st.warning("O nome não pode ser vazio.")
            return

        full_path = f"{parent_path} > {name}" if parent_path else name
        level = len(full_path.split(" > "))

        try:
            category_repo.insert({
                "name": name,
                "description": new_desc.strip(),
                "level": level,
                "parent_id": parent_id,
                "full_path": full_path,
            })
            st.success(f"✅ '{full_path}' criada!")
            st.session_state.pop(cancel_key, None)
            st.rerun()
        except Exception as exc:
            if "E11000" in str(exc) or "duplicate" in str(exc).lower():
                st.error(f"Já existe uma categoria com o caminho '{full_path}'.")
            else:
                st.error(f"Erro ao criar: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# ABA: Regras de Classificação
# ═══════════════════════════════════════════════════════════════════════════════

def _render_rules_tab() -> None:
    from database.repositories.rule_repo import rule_repo
    from database.repositories.category_repo import category_repo

    st.subheader("Regras de Classificação")
    st.caption(
        "Mapeiam padrões de texto para categorias. "
        "São criadas automaticamente ao confirmar categorizações, "
        "ou manualmente aqui."
    )

    show_inactive = st.toggle("Mostrar inativas", value=False, key="rule_show_inactive")

    col_header, col_add = st.columns([4, 1])
    col_header.markdown("### Lista de Regras")
    if col_add.button("➕ Nova regra", use_container_width=True):
        st.session_state["show_new_rule_form"] = True

    if st.session_state.get("show_new_rule_form"):
        _render_new_rule_form(rule_repo, category_repo)
        st.divider()

    try:
        rules = rule_repo.find_all(active_only=not show_inactive)
    except Exception as exc:
        st.error(f"Erro ao carregar regras: {exc}")
        return

    if not rules:
        st.info("Nenhuma regra encontrada.")
        return

    cols = st.columns([3, 2, 3, 1, 1, 1, 1])
    for col, h in zip(cols, ["Padrão", "Tipo", "Categoria", "Prior.", "Usos", "Último uso", ""]):
        col.markdown(f"**{h}**")
    st.divider()

    for rule in rules:
        _render_rule_row(rule, rule_repo)


def _render_rule_row(rule: dict, rule_repo) -> None:
    is_active = rule.get("is_active", True)
    match_labels = {
        "contains":    "Contém",
        "starts_with": "Começa com",
        "exact":       "Exato",
        "regex":       "Regex",
    }
    last_used = rule.get("last_used_at")

    cols = st.columns([3, 2, 3, 1, 1, 1, 1])
    pattern_txt = f"`{rule.get('pattern', '')}`"
    cols[0].markdown(pattern_txt if is_active else f"~~{pattern_txt}~~")
    cols[1].write(match_labels.get(rule.get("match_type", ""), rule.get("match_type", "")))
    cols[2].write(rule.get("category", {}).get("full_path", "—"))
    cols[3].write(rule.get("priority", 10))
    cols[4].write(rule.get("hit_count", 0))
    cols[5].write(_fmt_date(last_used) if last_used else "—")

    rid = rule.get("_id", "")
    if is_active:
        if cols[6].button("🚫", key=f"deact_rule_{rid}", help="Desativar"):
            rule_repo.deactivate(rid)
            st.rerun()
    else:
        if cols[6].button("✅", key=f"react_rule_{rid}", help="Reativar"):
            rule_repo.reactivate(rid)
            st.rerun()


def _render_new_rule_form(rule_repo, category_repo) -> None:
    st.markdown("#### Nova Regra Manual")
    try:
        all_cats = category_repo.find_all(active_only=True)
        cat_paths = [c["full_path"] for c in all_cats]
    except Exception:
        cat_paths = []

    with st.form("form_nova_regra", clear_on_submit=True):
        c1, c2 = st.columns(2)
        pattern = c1.text_input("Padrão (lowercase, sem acentos)", placeholder="Ex: ifood, uber, posto")
        match_type = c2.selectbox(
            "Tipo",
            ["contains", "starts_with", "exact", "regex"],
            format_func=lambda t: {
                "contains": "Contém", "starts_with": "Começa com",
                "exact": "Exato", "regex": "Expressão regular",
            }[t],
        )
        c3, c4 = st.columns([3, 1])
        selected_path = c3.selectbox("Categoria", ["— Selecione —"] + cat_paths)
        priority = c4.number_input("Prioridade", min_value=1, max_value=100, value=10)

        c_save, c_cancel = st.columns(2)
        save = c_save.form_submit_button("Criar Regra", type="primary")
        cancel = c_cancel.form_submit_button("Cancelar")

    if cancel:
        st.session_state.pop("show_new_rule_form", None)
        st.rerun()

    if save:
        if not pattern.strip() or selected_path == "— Selecione —":
            st.warning("Preencha padrão e categoria.")
            return

        parts = selected_path.split(" > ")
        category = {
            "level1_id":   None,
            "level1_name": parts[0] if parts else "",
            "level2_id":   None,
            "level2_name": parts[1] if len(parts) > 1 else None,
            "full_path":   selected_path,
        }

        try:
            rule_repo.insert({
                "pattern":    pattern.strip().lower(),
                "match_type": match_type,
                "category":   category,
                "priority":   int(priority),
            })
            st.success(f"Regra criada: `{pattern}` → {selected_path}")
            st.session_state.pop("show_new_rule_form", None)
            st.rerun()
        except Exception as exc:
            st.error(f"Erro: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# ABA: Configurações do Sistema
# ═══════════════════════════════════════════════════════════════════════════════

def _render_config_tab() -> None:
    from config.settings import settings

    st.subheader("Configurações Ativas")
    st.caption("Lidas do arquivo `.env`. Reinicie a aplicação após alterações.")

    st.markdown("**MongoDB**")
    c1, c2 = st.columns(2)
    c1.text_input("URI", value=settings.mongo_uri, disabled=True)
    c2.text_input("Banco", value=settings.mongo_db, disabled=True)

    st.divider()
    st.markdown("**Anthropic API**")
    c3, c4, c5 = st.columns(3)
    api_display = (
        settings.anthropic_api_key[:8] + "***"
        if settings.anthropic_api_key else "⚠️ Não configurada"
    )
    c3.text_input("API Key", value=api_display, disabled=True)
    c4.text_input("Modelo Categorizador", value=settings.anthropic_model_categorizer, disabled=True)
    c5.text_input("Modelo Chat", value=settings.anthropic_model_chat, disabled=True)

    if not settings.anthropic_configured:
        st.warning("ANTHROPIC_API_KEY não configurada — IA indisponível.")

    st.divider()
    st.markdown("**Aplicação**")
    c6, c7 = st.columns(2)
    c6.text_input("Ambiente", value=settings.app_env, disabled=True)
    c7.text_input("Debug", value="Ativado" if settings.app_debug else "Desativado", disabled=True)

    st.divider()
    st.caption("Edite `.env` na raiz do projeto e reinicie para aplicar mudanças.")


# ── Utilitários ────────────────────────────────────────────────────────────────

def _fmt_date(dt) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%d/%m/%Y") if hasattr(dt, "strftime") else str(dt)[:10]
    except Exception:
        return str(dt)[:10]
