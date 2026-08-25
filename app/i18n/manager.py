# app/i18n/manager.py
# LAYER: Infrastructure / Internationalization (Logic)
# PURPOSE: Contains the core logic for fetching, formatting, and falling back on translations.
# WHY HERE: Centralizes the i18n engine. The UI and Service layers just call `get_text()` 
# without needing to know how the dictionaries are structured or loaded.

from __future__ import annotations

from typing import Any

from app.i18n.am import STRINGS as AM

# Import the raw dictionaries from the language files
from app.i18n.en import STRINGS as EN

# Fallback language if the user's language is missing or invalid
DEFAULT_LANGUAGE = "en"

# Registry of all supported languages. 
# To add a new language, import its STRINGS dict and add it here.
SUPPORTED_LANGUAGES: dict[str, dict[str, str]] = {
    "en": EN,
    "am": AM,
}

def supported_language(code: str | None) -> str:
    """
    Validates and normalizes a language code.
    Returns the default language if the provided code is not supported.
    """
    if not code:
        return DEFAULT_LANGUAGE
    code = code.strip().lower()
    if code in SUPPORTED_LANGUAGES:
        return code
    return DEFAULT_LANGUAGE

def get_text(language: str | None, key: str, **kwargs: Any) -> str:
    """
    Fetches a translated string by key, with automatic fallback and variable formatting.
    
    1. Checks the user's preferred language.
    2. Falls back to English if the key is missing in the preferred language.
    3. Falls back to the raw key if it's missing in English (prevents crashes).
    4. Safely formats variables (e.g., {request_number}) into the string.
    """
    lang = supported_language(language)
    catalog = SUPPORTED_LANGUAGES[lang]
    
    # Fetch from target language, fallback to English, fallback to raw key
    value = catalog.get(key)
    if value is None:
        value = SUPPORTED_LANGUAGES[DEFAULT_LANGUAGE].get(key, key)
        
    # Format variables if provided (e.g., get_text("en", "request.submitted", request_number="REQ-123"))
    if kwargs:
        try:
            return value.format(**kwargs)
        except Exception:
            # If formatting fails (e.g., missing key in kwargs), return the unformatted string 
            # rather than crashing the bot.
            return value
    return value

def status_text(language: str | None, status_value: str) -> str:
    """
    Helper specifically for translating Enum status values.
    Automatically prefixes the key with 'status.' to match the dictionary structure.
    """
    return get_text(language, f"status.{status_value}")

def translate_error(language: str | None, exc: Exception) -> str:
    """
    Translates Domain Exceptions into user-friendly messages.
    Extracts the i18n key and parameters from LocalizedDomainError instances.
    """
    # If it's a LocalizedDomainError, it has a specific translation key and params
    key = getattr(exc, "key", None) or str(exc)
    params = getattr(exc, "params", None) or {}
    return get_text(language, key, **params)