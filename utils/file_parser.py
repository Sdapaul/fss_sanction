"""첨부파일(PDF·DOCX)에서 텍스트를 추출하는 유틸리티."""
import logging
import os

logger = logging.getLogger(__name__)

_MAX_CHARS = 2000


def extract_text(path: str, max_chars: int = _MAX_CHARS) -> str:
    """파일 확장자 또는 매직바이트로 형식을 판별해 텍스트를 추출."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".pdf", ".docx"):
        ext = _detect_type(path)
    try:
        if ext == ".pdf":
            return _from_pdf(path, max_chars)
        if ext == ".docx":
            return _from_docx(path, max_chars)
    except Exception as e:
        logger.warning(f"첨부파일 텍스트 추출 실패 ({os.path.basename(path)}): {e}")
    return ""


def _detect_type(path: str) -> str:
    """파일 매직바이트로 PDF·DOCX 여부를 판별."""
    try:
        with open(path, "rb") as f:
            header = f.read(8)
        if header[:4] == b"%PDF":
            return ".pdf"
        # DOCX/XLSX/ZIP은 모두 PK 헤더로 시작; DOCX만 구분하려면 ZIP 내부 확인 필요
        if header[:4] == b"PK\x03\x04":
            import zipfile
            with zipfile.ZipFile(path) as z:
                if "word/document.xml" in z.namelist():
                    return ".docx"
    except Exception:
        pass
    return ""


def _from_pdf(path: str, max_chars: int) -> str:
    import pdfplumber
    parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                parts.append(t.strip())
            if sum(len(p) for p in parts) >= max_chars:
                break
    return "\n".join(parts)[:max_chars]


def _from_docx(path: str, max_chars: int) -> str:
    from docx import Document
    doc = Document(path)
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(parts)[:max_chars]
