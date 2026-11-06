from io import BytesIO
from pathlib import Path


def parse_document(filename, data):
    if not data or len(data) > 10_000_000:
        raise ValueError("文件必须非空且不超过 10 MB")
    extension = Path(filename).suffix.lower()
    if extension in {".txt", ".md"}:
        text = data.decode("utf-8-sig")
    elif extension == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > 200:
            raise ValueError("暂不支持加密 PDF 或超过 200 页的文档")
        text = "\n\n".join(f"[第 {i} 页]\n{page.extract_text() or ''}" for i, page in enumerate(reader.pages, 1))
        if not any((page.extract_text() or '').strip() for page in reader.pages):
            raise ValueError("PDF 无文本层，需先完成 OCR")
    elif extension == ".docx":
        from docx import Document
        doc = Document(BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)
        for table in doc.tables:
            text += "\n" + "\n".join(" | ".join(cell.text for cell in row.cells) for row in table.rows)
    else:
        raise ValueError("仅支持 Markdown、TXT、原生文本 PDF 和 DOCX")
    if not text.strip() or len(text) > 100000:
        raise ValueError("文档文本为空或超过 100000 字符")
    return text
