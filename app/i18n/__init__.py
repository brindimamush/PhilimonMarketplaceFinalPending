# app/i18n/__init__.py
from app.i18n.manager import get_text, status_text, supported_language, translate_error

__all__ = [
    "get_text",
    "status_text",
    "supported_language",
    "translate_error",
]