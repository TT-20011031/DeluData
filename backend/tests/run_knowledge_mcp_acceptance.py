"""Live acceptance harness for the Knowledge MCP answer workflow.

Run inside an environment with the production-like MCP, database, model, and
Chroma settings. The script exits non-zero when any repeated answer drifts.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.services.knowledge_mcp_service import KnowledgeMcpService
from app.skills.doc_skill import DocSkill


PRICE_QUERY = "杭州泰泽办公设备有限公司提供的打印机历史价格是多少？"
COMPOSITE_QUERY = (
    "杭州泰泽办公设备有限公司的企业资质怎么样？\n"
    "它提供的打印机历史价格是否合理，可否作为采购参考？"
)
TARGET_FILE = "24年-1-杭州泰泽办公设备有限公司.pdf"
FORBIDDEN_FILES = ("浙江欣赞", "盛郑艺唐")


def _evaluate(payload: dict) -> tuple[bool, list[str]]:
    answer = str(payload.get("answer") or "")
    normalized_answer = answer.replace(",", "").replace("，", "")
    citation_names = [
        str(item.get("original_file_name") or "")
        for item in payload.get("citations") or []
        if isinstance(item, dict)
    ]
    failures = []
    if TARGET_FILE not in citation_names:
        failures.append("target_file_missing")
    if "3700" not in normalized_answer:
        failures.append("large_printer_price_missing")
    if "290" not in normalized_answer:
        failures.append("desktop_printer_price_missing")
    if any(name in " ".join(citation_names) for name in FORBIDDEN_FILES):
        failures.append("unrelated_supplier_cited")
    return not failures, failures


async def _run(query: str, repeats: int) -> int:
    service = KnowledgeMcpService()
    failed = 0
    for index in range(1, repeats + 1):
        payload = await service.ask(query=query, reply_model_key="flash")
        ok, failures = _evaluate(payload)
        if not ok:
            failed += 1
        print(
            json.dumps(
                {
                    "run": index,
                    "ok": ok,
                    "failures": failures,
                    "status": payload.get("status"),
                    "answer": payload.get("answer"),
                    "citations": payload.get("citations") or [],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 1 if failed else 0


async def _run_exact(query: str) -> int:
    service = KnowledgeMcpService()
    chunks = await DocSkill()._exact_term_fallback_search(
        query=query,
        original_query=query,
        user_context=service.user_context(),
        top_k=10,
        include_images=False,
        file_ids=None,
        visibilities=None,
        dept_ids=None,
    )
    rows = [
        {
            "chunk_id": chunk.chunk_id,
            "source_file": chunk.source_file,
            "content": chunk.content,
            "metadata": chunk.metadata,
        }
        for chunk in chunks
    ]
    print(json.dumps(rows, ensure_ascii=False, default=str), flush=True)
    text = json.dumps(rows, ensure_ascii=False, default=str)
    return 0 if TARGET_FILE in text and "3700" in text and "290" in text else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--composite", action="store_true")
    parser.add_argument("--exact-only", action="store_true")
    args = parser.parse_args()
    query = COMPOSITE_QUERY if args.composite else PRICE_QUERY
    if args.exact_only:
        return asyncio.run(_run_exact(query))
    return asyncio.run(_run(query, max(1, args.repeats)))


if __name__ == "__main__":
    raise SystemExit(main())
