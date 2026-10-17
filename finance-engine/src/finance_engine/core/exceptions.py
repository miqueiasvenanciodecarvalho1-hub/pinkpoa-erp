"""Exceções de domínio. A camada HTTP as traduz em status codes (ver ``api.errors``)."""

from __future__ import annotations


class FinanceEngineError(Exception):
    """Base de todas as exceções do domínio."""

    #: Preenchido quando o erro ocorre ao processar um lote de importação já registrado.
    batch_id: str | None = None


class NotFoundError(FinanceEngineError):
    pass


class ConflictError(FinanceEngineError):
    pass


class DuplicateImportError(ConflictError):
    def __init__(self, message: str, existing_batch_id: str) -> None:
        super().__init__(message)
        self.existing_batch_id = existing_batch_id


class UnsupportedFormatError(FinanceEngineError):
    pass


class ParseError(FinanceEngineError):
    """Arquivo ilegível ou com proporção de erros acima do tolerado."""

    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message if line is None else f"Linha {line}: {message}")
        self.line = line


class RowParseError(ParseError):
    """Erro restrito a uma linha; o parser converte em aviso e segue adiante."""
