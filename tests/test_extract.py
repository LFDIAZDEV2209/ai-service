"""Regresiones de extracción: texto con NUL y PDFs cifrados con AES, sin red."""

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.rag.extract import extract_text


def _synthetic_pdf(*, encrypted: bool = False) -> bytes:
    """Genera un PDF con un NUL en el texto y contraseña de apertura vacía."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (antes\\000despues) Tj ET")
    page[NameObject("/Contents")] = stream
    if encrypted:
        writer.encrypt(user_password="", owner_password="test-owner", algorithm="AES-256")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("extension", ["md", "markdown", "txt", "text", "rst", "pdf"])
def test_extract_strips_nul_bytes(extension):
    content = _synthetic_pdf() if extension == "pdf" else b"antes\x00despues"
    if extension == "pdf":
        # Verifica que el parser realmente reproduce el NUL observado en producción.
        assert "\x00" in PdfReader(BytesIO(content)).pages[0].extract_text()

    text = extract_text(f"documento.{extension}", content)

    assert "\x00" not in text
    assert text == "antes despues"


def test_extract_aes_encrypted_pdf():
    content = _synthetic_pdf(encrypted=True)
    assert PdfReader(BytesIO(content)).is_encrypted
    assert extract_text("cifrado.pdf", content) == "antes despues"
