def split_text(text: str, size: int = 450, overlap: int = 60) -> list[dict]:
    """Fixed character windows; source line numbers survive splitting."""
    chunks = []
    for start in range(0, len(text), size - overlap):
        end = min(start + size, len(text))
        part = text[start:end]
        if part.strip():
            chunks.append({
                "text": part,
                "start_line": text.count("\n", 0, start) + 1,
                "end_line": text.count("\n", 0, end) + 1,
            })
        if end == len(text):
            break
    return chunks
