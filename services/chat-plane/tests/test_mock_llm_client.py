import asyncio
import os
import unittest
from unittest.mock import patch

from runtime.llm_client import LlmClient


class MockLlmClientTest(unittest.TestCase):
    def test_mock_stream_and_completion_do_not_require_api_pool(self):
        async def scenario():
            with patch.dict(os.environ, {"CHAT_PROVIDER_MODE": "mock"}):
                client = LlmClient()
                messages = [{"role": "user", "content": "powflow evidence alpha"}]
                streamed = "".join([chunk async for chunk in client.stream_chat(messages)])
                completed = await client.complete_text(messages)
            return streamed, completed

        streamed, completed = asyncio.run(scenario())
        self.assertIn("powflow evidence alpha", streamed)
        self.assertEqual(streamed, completed)
        self.assertIn("未调用外部模型", completed)

    def test_mock_embedding_is_deterministic_and_normalized(self):
        async def scenario():
            with patch.dict(os.environ, {"CHAT_PROVIDER_MODE": "mock"}):
                client = LlmClient()
                return await client.embed_text("相同文本"), await client.embed_text("相同文本")

        first, second = asyncio.run(scenario())
        self.assertEqual(first, second)
        self.assertEqual(256, len(first))
        self.assertAlmostEqual(1.0, sum(value * value for value in first), places=6)


if __name__ == "__main__":
    unittest.main()
