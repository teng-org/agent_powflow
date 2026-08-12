from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retrieval.service import _chunk_text, preprocess_text_for_chunk
from retrieval.structured_chunking import parse_content_blocks


class ChunkingIntegrityTest(unittest.TestCase):
    def test_fixed_mode_uses_exact_windows_and_preserves_all_content(self):
        source = "".join(str(index % 10) for index in range(2350))
        chunks = _chunk_text(source, chunk_size=1000, chunk_overlap=100, chunk_method="fixed")

        self.assertEqual([1000, 1000, 550], [len(chunk) for chunk in chunks])
        self.assertEqual(source[0:1000], chunks[0])
        self.assertEqual(source[900:1900], chunks[1])
        self.assertEqual(source[1800:2350], chunks[2])
        self.assertEqual(source, chunks[0] + chunks[1][100:] + chunks[2][100:])

    def test_preprocessing_removes_noise_without_dropping_text(self):
        source = "  第一段  \t 测试\n   \n\n\n第二段  test   value  "
        cleaned = preprocess_text_for_chunk(source)
        self.assertEqual("第一段测试\n\n第二段 test value", cleaned)

    def test_page_mode_keeps_non_empty_source_pages_separate(self):
        chunks = _chunk_text(
            "first page\f\fsecond page",
            chunk_size=500,
            chunk_overlap=50,
            chunk_method="page",
        )

        self.assertEqual(["first page", "second page"], chunks)

    def test_preprocessing_does_not_strip_physical_page_markers(self):
        self.assertEqual("\fpage two\f", preprocess_text_for_chunk(" \n\fpage two\f\n "))

    def test_structured_headings_remain_in_chunk_text(self):
        markdown_heading = "# Retrieval Architecture"
        plain_heading = "1. Fixed Chunking"
        blocks = parse_content_blocks(
            f"{markdown_heading}\n\nHeading body.\n\n{plain_heading}\n\nFixed window body."
        )
        combined = "\n".join(block.text for block in blocks)
        self.assertIn(markdown_heading, combined)
        self.assertIn(plain_heading, combined)


if __name__ == "__main__":
    unittest.main()
