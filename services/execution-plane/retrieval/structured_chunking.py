from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re


@dataclass(frozen=True)
class ContentBlock:
    block_index: int
    text: str
    block_type: str
    section_path: tuple[str, ...]
    page_start: int
    page_end: int


@dataclass(frozen=True)
class ChildChunk:
    text: str
    parent_id: str
    parent_index: int
    parent_text: str
    section_path: tuple[str, ...]
    block_type: str
    page_start: int
    page_end: int

    def metadata(self) -> dict:
        value = asdict(self)
        value.pop("text", None)
        value.pop("parent_text", None)
        value["section_path"] = list(self.section_path)
        return value


_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PLAIN_HEADING = re.compile(r"^(?:第[一二三四五六七八九十百0-9]+[章节部分]|[0-9]+(?:\.[0-9]+)*[、.])\s*\S+")


def _block_type(text: str, fenced: bool = False) -> str:
    value = text.strip()
    if fenced:
        return "code"
    lines = [line for line in value.splitlines() if line.strip()]
    if lines and all("|" in line for line in lines[: min(3, len(lines))]):
        return "table"
    if lines and all(re.match(r"^\s*(?:[-*+] |\d+[.)] )", line) for line in lines):
        return "list"
    return "paragraph"


def parse_content_blocks(text: str) -> list[ContentBlock]:
    """Create deterministic, citation-friendly blocks from plain/Markdown text.

    Form-feed characters preserve page boundaries from PDF extraction. Markdown
    headings and common Chinese/numbered headings are retained as section paths.
    """
    blocks: list[ContentBlock] = []
    section_stack: list[str] = []
    buffer: list[str] = []
    page = 1
    block_page = 1
    fenced = False

    def flush() -> None:
        nonlocal buffer, block_page
        value = "\n".join(buffer).strip()
        if value:
            blocks.append(ContentBlock(
                block_index=len(blocks),
                text=value,
                block_type=_block_type(value, fenced=fenced),
                section_path=tuple(section_stack),
                page_start=block_page,
                page_end=page,
            ))
        buffer = []
        block_page = page

    for raw_line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if "\f" in raw_line:
            parts = raw_line.split("\f")
        else:
            parts = [raw_line]
        for part_index, line in enumerate(parts):
            stripped = line.strip()
            heading = _MARKDOWN_HEADING.match(stripped)
            if not fenced and heading:
                flush()
                level = len(heading.group(1))
                title = heading.group(2).strip()
                section_stack[:] = section_stack[: level - 1]
                section_stack.append(title)
            elif not fenced and stripped and len(stripped) <= 100 and _PLAIN_HEADING.match(stripped):
                flush()
                section_stack[:] = [stripped]
            elif stripped.startswith("```"):
                if fenced:
                    buffer.append(line)
                    flush()
                    fenced = False
                else:
                    flush()
                    fenced = True
                    block_page = page
                    buffer.append(line)
            elif not fenced and not stripped:
                flush()
            else:
                if not buffer:
                    block_page = page
                buffer.append(line)
            if part_index < len(parts) - 1:
                flush()
                page += 1
                block_page = page
    flush()
    return blocks


def _split_child(text: str, size: int, overlap: int) -> list[str]:
    value = text.strip()
    if not value:
        return []
    size = max(100, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    if len(value) <= size:
        return [value]
    out: list[str] = []
    start = 0
    step = max(1, size - overlap)
    while start < len(value):
        piece = value[start : start + size].strip()
        if piece:
            out.append(piece)
        start += step
    return out


def build_parent_child_chunks(
    blocks: list[ContentBlock],
    *,
    identity: str,
    child_size: int,
    child_overlap: int,
    max_child_chars: int | None = None,
    parent_size: int | None = None,
) -> list[ChildChunk]:
    """Group structured blocks into parent context and smaller recall chunks."""
    effective_child_size = max(100, min(int(child_size), int(max_child_chars or child_size)))
    effective_parent_size = max(effective_child_size, int(parent_size or max(1200, effective_child_size * 3)))
    parents: list[list[ContentBlock]] = []
    current: list[ContentBlock] = []
    current_chars = 0
    current_section: tuple[str, ...] | None = None
    for block in blocks:
        section_changed = current_section is not None and block.section_path != current_section
        would_overflow = current and current_chars + len(block.text) + 2 > effective_parent_size
        if section_changed or would_overflow:
            parents.append(current)
            current = []
            current_chars = 0
        current.append(block)
        current_chars += len(block.text) + 2
        current_section = block.section_path
    if current:
        parents.append(current)

    children: list[ChildChunk] = []
    for parent_index, group in enumerate(parents):
        parent_text = "\n\n".join(block.text for block in group).strip()
        parent_key = f"{identity}:{parent_index}:{parent_text}"
        parent_id = hashlib.sha1(parent_key.encode("utf-8")).hexdigest()  # nosec B324
        for block in group:
            for piece in _split_child(block.text, effective_child_size, child_overlap):
                children.append(ChildChunk(
                    text=piece,
                    parent_id=parent_id,
                    parent_index=parent_index,
                    parent_text=parent_text,
                    section_path=block.section_path,
                    block_type=block.block_type,
                    page_start=block.page_start,
                    page_end=block.page_end,
                ))
    return children
