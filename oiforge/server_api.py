"""Publiczne API dla modułów narzędzi (oiforge/tools/*)."""


class ApiError(Exception):
    """Błąd zgłaszany przez handler — trafia do użytkownika jako {"ok": false, "error": message}."""

    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra
