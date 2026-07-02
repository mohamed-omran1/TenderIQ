"""
REQ-004 Accuracy Eval — Standalone measurement script.

Usage:
    python eval/run_eval.py --tender-id <uuid>

Loads eval/labelled_sample_tender.json and runs the real risk_radar_node
against the labelled tender's chunks (fetched from DB by tender_id).
Computes recall, precision, and F1 against the ground truth.

Exits with code 0 if recall >= 85%, code 1 otherwise.
If labelled_findings is empty (placeholder state), prints a skip message
and exits with code 0.

This is NOT a pytest test. It is a one-time (or periodic) accuracy
measurement that becomes the baseline for REQ-012's automated eval harness.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.nodes.risk_radar import risk_radar_node
from app.agents.state import TenderState
from app.db.models import TenderChunk

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _load_ground_truth(path: str) -> dict:
    """Load the labelled ground-truth file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data


async def _fetch_chunks(tender_id: str, db_url: str) -> list[dict]:
    """Fetch all chunks for a tender from the database."""
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession)
    async with session_factory() as session:
        result = await session.execute(
            select(TenderChunk)
            .where(TenderChunk.tender_id == tender_id)
            .order_by(TenderChunk.chunk_index)
        )
        chunks = []
        for row in result.scalars():
            chunks.append({
                "content": row.content,
                "detected_language": row.detected_language,
                "chunk_index": row.chunk_index,
            })
    await engine.dispose()
    return chunks


def _compute_recall(
    labelled: list[dict],
    model_findings: list[dict],
) -> float:
    """How many labelled findings were found by the model.

    Match by clause_text substring overlap >= 0.7 (Jaccard-like).
    """
    if not labelled:
        return 0.0

    found = 0
    for lf in labelled:
        for mf in model_findings:
            a = lf["clause_text"].lower()
            b = mf["clause_text"].lower()
            if not a or not b:
                continue
            overlap = len(set(a.split()) & set(b.split())) / max(
                len(set(a.split()) | set(b.split())), 1
            )
            if overlap >= 0.7:
                found += 1
                break
    return found / len(labelled)


def _compute_precision(
    labelled: list[dict],
    model_findings: list[dict],
) -> float:
    """How many model findings match a labelled finding."""
    if not model_findings:
        return 0.0

    matched = 0
    for mf in model_findings:
        for lf in labelled:
            a = lf["clause_text"].lower()
            b = mf["clause_text"].lower()
            if not a or not b:
                continue
            overlap = len(set(a.split()) & set(b.split())) / max(
                len(set(a.split()) | set(b.split())), 1
            )
            if overlap >= 0.7:
                matched += 1
                break
    return matched / len(model_findings)


async def main() -> int:
    parser = argparse.ArgumentParser(description="REQ-004 Accuracy Evaluation")
    parser.add_argument(
        "--tender-id",
        required=True,
        help="UUID of the tender whose chunks to evaluate against.",
    )
    parser.add_argument(
        "--ground-truth",
        default=None,
        help="Path to labelled_sample_tender.json (default: eval/labelled_sample_tender.json adjacent to this script).",
    )
    args = parser.parse_args()

    # Locate ground truth file.
    gt_path = args.ground_truth or (
        Path(__file__).resolve().parent / "labelled_sample_tender.json"
    )
    ground_truth = _load_ground_truth(str(gt_path))

    # Check for placeholder.
    if not ground_truth.get("labelled_findings"):
        logger.info("Eval skipped — no labelled tender available yet.")
        logger.info(
            "REQ-004 Accuracy Eval\n"
            "  Tender: %s\n"
            "  Status: SKIPPED (placeholder)",
            ground_truth.get("tender_name", "N/A"),
        )
        return 0

    # Build state and run the real node.
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/tenderiq",
    )
    chunks = await _fetch_chunks(args.tender_id, db_url)

    state = dict(TenderState(
        tender_id=args.tender_id,
        run_id=str(uuid.uuid4()),
        company_id="",
        chunks=chunks,
        supervisor_ready=False,
        risk_findings=[],
        feasibility_score=None,
        feasibility_breakdown=None,
        financial_summary=None,
        aggregated_results=None,
        hitl_approved=False,
        hitl_override_score=None,
        final_report=None,
        token_usage=[],
        source_languages=[],
    ))
    config = {"configurable": {"thread_id": state["run_id"]}}
    result = await risk_radar_node(state, config)
    model_findings = result.get("risk_findings", [])

    labelled = ground_truth["labelled_findings"]

    recall = _compute_recall(labelled, model_findings) * 100
    precision = _compute_precision(labelled, model_findings) * 100
    f1 = 2 * (recall * precision) / max(recall + precision, 0.01)

    status = "PASS" if recall >= 85.0 else "FAIL"

    logger.info(
        "REQ-004 Accuracy Eval\n"
        "  Tender: %s\n"
        "  Labelled findings: %d\n"
        "  Model findings:    %d\n"
        "  Recall:    %.1f%% (target: >= 85%%)\n"
        "  Precision: %.1f%%\n"
        "  F1:        %.1f%%\n"
        "  Status: %s",
        ground_truth.get("tender_name", "N/A"),
        len(labelled),
        len(model_findings),
        recall,
        precision,
        f1,
        status,
    )

    return 0 if recall >= 85.0 else 1


if __name__ == "__main__":
    import asyncio

    exit_code = asyncio.run(main())
    sys.exit(exit_code)
