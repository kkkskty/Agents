from __future__ import annotations

import hashlib
import re
from typing import List, Tuple


def read_markdown(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_clause_no(head: str) -> str:
    clean_head = head.strip("# ").strip()
    m = re.match(r"^((?:\d+\.)+\d*).*", clean_head)
    if m:
        return m.group(1).rstrip(".")
    return "H-" + sha256_text(clean_head)[:8]


def split_clauses(markdown_text: str, chunk_max_chars: int, chunk_overlap_chars: int) -> List[Tuple[str, str, int]]:
    lines = markdown_text.splitlines()
    head_pattern = re.compile(r"^(#{1,6}\s+.+|(?:\d+\.)+\d*\s*.+)$")

    blocks: List[Tuple[str, str]] = []
    current_head = "intro"
    current_buf: List[str] = []

    for line in lines:
        if head_pattern.match(line.strip()):
            if current_buf:
                blocks.append((current_head, "\n".join(current_buf).strip()))
                current_buf = []
            current_head = line.strip()
        else:
            current_buf.append(line)

    if current_buf:
        blocks.append((current_head, "\n".join(current_buf).strip()))

    chunks: List[Tuple[str, str, int]] = []
    order_no = 1
    for head, body in blocks:
        text = (head + "\n" + body).strip()
        if not text:
            continue

        clause_no = extract_clause_no(head)
        if len(text) <= chunk_max_chars:
            chunks.append((clause_no, text, order_no))
            order_no += 1
            continue

        start = 0
        while start < len(text):
            end = min(start + chunk_max_chars, len(text))
            piece = text[start:end].strip()
            if piece:
                chunks.append((clause_no, piece, order_no))
                order_no += 1
            if end == len(text):
                break
            start = max(0, end - chunk_overlap_chars)

    return chunks
