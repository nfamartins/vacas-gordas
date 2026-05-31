"""Páginas da aplicação Vacas Gordas."""
from ui.pages.categorization import render as render_categorization
from ui.pages.chat_page import render as render_chat
from ui.pages.dashboard import render as render_dashboard
from ui.pages.import_page import render as render_import
from ui.pages.settings_page import render as render_settings
from ui.pages.transactions import render as render_transactions

__all__ = [
    "render_dashboard",
    "render_import",
    "render_categorization",
    "render_transactions",
    "render_chat",
    "render_settings",
]
