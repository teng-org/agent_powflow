import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from models.embedding_provider import EmbeddingProvider


class EmbeddingProviderTest(unittest.TestCase):
    def test_local_only_never_resolves_external_endpoint(self):
        with patch.dict(os.environ, {"EMBEDDING_MODE": "local_only"}):
            provider = EmbeddingProvider()
            first = provider.embed("tenant-a", "powflow evidence alpha", local_dimensions=32)
            second = provider.embed("tenant-a", "powflow evidence alpha", local_dimensions=32)

        self.assertEqual(first, second)
        self.assertEqual(32, len(first))
        self.assertAlmostEqual(1.0, sum(value * value for value in first), places=6)


if __name__ == "__main__":
    unittest.main()
