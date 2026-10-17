"""Detecção de formato pelo conteúdo (o nome do arquivo é só um desempate)."""

from __future__ import annotations

from finance_engine.core.exceptions import UnsupportedFormatError
from finance_engine.models.enums import SourceFormat

_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # .xls (Excel 97-2003)


def detect_format(filename: str, content: bytes) -> SourceFormat:
    if not content.strip():
        raise UnsupportedFormatError("arquivo vazio")
    head = content[:2048]
    upper_head = head.upper()
    lower_name = filename.lower()

    if b"OFXHEADER" in upper_head or b"<OFX>" in upper_head:
        return SourceFormat.OFX
    if head.startswith(_ZIP_MAGIC):
        if lower_name.endswith((".xlsx", ".xlsm")) or b"xl/" in content[:4096]:
            return SourceFormat.XLSX
        raise UnsupportedFormatError("arquivo compactado não suportado (envie .xlsx, .ofx ou .csv)")
    if head.startswith(_OLE_MAGIC):
        raise UnsupportedFormatError("formato .xls antigo não suportado; salve como .xlsx")

    lines = [ln.rstrip(b"\r") for ln in content.split(b"\n") if ln.strip()]
    lengths = {len(ln) for ln in lines[:50]}
    if lengths and lengths <= {240}:
        return SourceFormat.CNAB240
    if lengths and lengths <= {400}:
        return SourceFormat.CNAB400
    if lower_name.endswith((".ret", ".rem", ".cnab")):
        raise UnsupportedFormatError("arquivo CNAB com registros de tamanho irregular")
    if lower_name.endswith((".csv", ".txt")) or b";" in head or b"," in head:
        return SourceFormat.CSV
    raise UnsupportedFormatError(f"formato não reconhecido: {filename}")
