def split_text(text: str, size: int = 450, overlap: int = 60, strategy: str = "fixed") -> list[dict]:
    """Fixed character windows; source line numbers survive splitting."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("分块大小或重叠无效")
    if strategy == "structure":
        return split_structure(text, size, overlap)
    if strategy != "fixed":
        raise ValueError("未知分块策略")
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


def split_structure(text, size, overlap):
    """Keep heading/paragraph/table blocks intact when they fit; never invent source text."""
    import re
    boundaries = [0] + [m.start() for m in re.finditer(r"(?m)^#{1,6}\s|(?<=\n)\n+", text)] + [len(text)]
    boundaries = sorted(set(boundaries))
    chunks, start, end = [], None, None

    def emit(a, b):
        if text[a:b].strip():
            chunks.append({"text": text[a:b], "start_line": text.count("\n", 0, a) + 1,
                           "end_line": text.count("\n", 0, b) + 1})

    for a, b in zip(boundaries, boundaries[1:]):
        if b - a > size:
            if start is not None:
                emit(start, end)
                start = None
            for offset in range(a, b, size - overlap):
                emit(offset, min(offset + size, b))
                if offset + size >= b:
                    break
        elif start is None:
            start, end = a, b
        elif b - start <= size and not text[a:b].lstrip().startswith("#"):
            end = b
        else:
            emit(start, end)
            start, end = a, b
    if start is not None:
        emit(start, end)
    return chunks
