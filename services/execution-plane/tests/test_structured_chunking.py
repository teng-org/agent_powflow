from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retrieval.structured_chunking import build_parent_child_chunks, parse_content_blocks


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
        self.assertTrue(all(item.parent_text.startswith("检索内容") for item in chunks))
        self.assertEqual(("章节",), chunks[0].section_path)


if __name__ == "__main__":
    unittest.main()
