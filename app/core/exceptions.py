# app/core/exceptions.py
# LAYER: Domain / Core
# PURPOSE: Defines custom exceptions for business logic failures.
# WHY HERE: Separates business errors (e.g., "Request already closed") from infrastructure errors (e.g., "DB timeout").

from typing import Any


class DomainError(Exception): pass

class LocalizedDomainError(DomainError):
    # Exception that carries an i18n translation key and parameters
    def __init__(self, key: str, **params: Any):
        self.key = key
        self.params = params
        super().__init__(key)

class SuspendedError(DomainError): pass
class NotFoundError(DomainError): pass
class ConflictError(DomainError): pass