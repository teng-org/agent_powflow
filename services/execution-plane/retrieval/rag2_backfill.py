from __future__ import annotations

import argparse
import json

from retrieval.store import kb_store


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill deterministic RAG 2.0 lexical features")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, default=0, help="0 means run until complete")
    args = parser.parse_args()
    batches = 0
    processed = 0
    while args.max_batches <= 0 or batches < args.max_batches:
        result = kb_store.backfill_rag2_lexical_features(args.batch_size)
        batches += 1
        processed += int(result.get("processed") or 0)
        print(json.dumps({"batch": batches, **result}, ensure_ascii=False), flush=True)
        if not result.get("has_more") or int(result.get("processed") or 0) == 0:
            break
    parent_batches = 0
    parent_documents = 0
    while args.max_batches <= 0 or parent_batches < args.max_batches:
        result = kb_store.backfill_parent_child_structure(max(1, min(args.batch_size, 500)))
        parent_batches += 1
        parent_documents += int(result.get("documents_processed") or 0)
        print(json.dumps({"parent_batch": parent_batches, **result}, ensure_ascii=False), flush=True)
        if not result.get("has_more") or int(result.get("documents_processed") or 0) == 0:
            break
    print(json.dumps({
        "status": "complete",
        "lexical_batches": batches,
        "lexical_chunks_processed": processed,
        "parent_batches": parent_batches,
        "parent_documents_processed": parent_documents,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
