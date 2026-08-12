from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retrieval.structured_chunking import build_page_chunks, build_parent_child_chunks, parse_content_blocks


class StructuredChunkingTest(unittest.TestCase):
    def test_preserves_sections_pages_tables_and_code(self):
        blocks = parse_content_blocks(
            "# 安全策略\n\n第一段说明。\f## 权限矩阵\n\n角色 | 权限\n管理员 | 全部\n\n```python\nprint('ok')\n```"
        )

        self.assertEqual(("安全策略",), blocks[0].section_path)
        self.assertEqual(1, blocks[0].page_start)
        self.assertEqual(("安全策略", "权限矩阵"), blocks[1].section_path)
        self.assertEqual("table", blocks[1].block_type)
        self.assertEqual(2, blocks[1].page_start)
        self.assertEqual("code", blocks[2].block_type)

    def test_children_share_a_persistent_parent_and_respect_provider_limit(self):
        blocks = parse_content_blocks("# 章节\n\n" + "检索内容" * 180)
        chunks = build_parent_child_chunks(
            blocks,
            identity="tenant:kb:doc",
            child_size=500,
            child_overlap=40,
            max_child_chars=380,
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(item.text) <= 380 for item in chunks))
        self.assertEqual(1, len({item.parent_id for item in chunks}))
        self.assertTrue(all("检索内容" in item.parent_text for item in chunks))
        self.assertEqual(("章节",), chunks[0].section_path)


    def test_page_chunks_preserve_page_metadata_and_split_only_oversized_pages(self):
        chunks = build_page_chunks(
            "page one\f" + ("x" * 250) + "\fpage three",
            identity="tenant:kb:doc",
            max_child_chars=100,
            overflow_overlap=20,
        )

        self.assertEqual("page one", chunks[0].text)
        self.assertEqual(1, chunks[0].page_start)
        self.assertEqual(1, chunks[0].page_end)
        page_two = [item for item in chunks if item.page_start == 2]
        self.assertGreater(len(page_two), 1)
        self.assertTrue(all(item.page_end == 2 and item.block_type == "page" for item in page_two))
        self.assertEqual(1, len({item.parent_id for item in page_two}))
        self.assertEqual("page three", chunks[-1].text)
        self.assertEqual(3, chunks[-1].page_start)


if __name__ == "__main__":
    unittest.main()
