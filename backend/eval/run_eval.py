"""
REQ-012 Evaluation Harness — Automated Accuracy Measurement (Slice 1: Eval Logic).

Usage:
    python eval/run_eval.py --tender-id <uuid> [--company-id <uuid>] [--risk] [--scorer] [--output json|text]

Loads eval/labelled_sample_tender.json and runs risk_radar_node and/or
feasibility_scorer_node against tender chunks from the database.
Computes recall, precision, F1 (risk radar) and score consistency (scorer).

Exits with code 0 on PASS, code 1 on FAIL/PARTIAL/NO_DATA.
If labelled_findings is empty (placeholder), risk eval is skipped with a note.

This is NOT a pytest test. It is a repeatable accuracy measurement for CI/CD.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.nodes.feasibility_scorer import feasibility_scorer_node
from app.agents.nodes.risk_radar import risk_radar_node
from app.agents.state import TenderState
from app.db.models import LlmCostEvent, Tender, TenderChunk
from eval.schemas import (
    CategoryMetrics,
    EvalRunResult,
    RiskRadarEvalResult,
    ScorerConsistencyResult,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CATEGORIES = ["fidic", "penalty", "lg_bond", "termination", "other"]
DEFAULT_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/tenderiq"


def _get_db_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DB_URL)


def _load_ground_truth(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def _fetch_chunks(tender_id: str, db_url: str) -> list[dict]:
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


async def _fetch_tender_name(tender_id: str, db_url: str) -> str:
    try:
        engine = create_async_engine(db_url)
        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession)
        async with session_factory() as session:
            result = await session.execute(
                select(Tender.filename).where(Tender.id == tender_id)
            )
            name = result.scalar_one_or_none()
        await engine.dispose()
        return name or "Unknown"
    except Exception:
        return "Unknown"


async def _fetch_total_cost(eval_run_ids: list[str], db_url: str) -> float:
    if not eval_run_ids:
        return 0.0
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession)
    async with session_factory() as session:
        result = await session.execute(
            select(func.coalesce(func.sum(LlmCostEvent.cost_usd), 0.0)).where(
                LlmCostEvent.run_id.in_(eval_run_ids)
            )
        )
        total = result.scalar_one()
    await engine.dispose()
    return float(total)


def compute_overlap(text_a: str, text_b: str) -> float:
    return SequenceMatcher(None, text_a.lower().strip(), text_b.lower().strip()).ratio()


def match_findings(
    model_findings: list[dict],
    labelled_findings: list[dict],
    threshold: float = 0.70,
) -> tuple[int, list[dict], list[dict]]:
    matched_pairs: list[dict] = []
    used_model_indices: set[int] = set()

    for li, labelled in enumerate(labelled_findings):
        best_score = 0.0
        best_mi = -1
        for mi, model in enumerate(model_findings):
            if mi in used_model_indices:
                continue
            score = compute_overlap(
                labelled.get("clause_text", ""),
                model.get("clause_text", ""),
            )
            if score > best_score and score >= threshold:
                best_score = score
                best_mi = mi
        if best_mi >= 0:
            used_model_indices.add(best_mi)
            matched_pairs.append({
                "labelled_index": li,
                "model_index": best_mi,
                "overlap": best_score,
            })

    matched_count = len(matched_pairs)
    labelled_matched_indices = {p["labelled_index"] for p in matched_pairs}
    unmatched_labelled = [
        lf for i, lf in enumerate(labelled_findings) if i not in labelled_matched_indices
    ]

    return matched_count, matched_pairs, unmatched_labelled


def _build_state(
    tender_id: str,
    run_id: str,
    company_id: str,
    chunks: list[dict],
    supervisor_ready: bool = True,
) -> dict:
    return dict(TenderState(
        tender_id=tender_id,
        run_id=run_id,
        company_id=company_id,
        chunks=chunks,
        supervisor_ready=supervisor_ready,
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


async def run_risk_radar_eval(
    tender_id: str,
    labelled_findings: list[dict],
    eval_run_id: str,
    db_url: str,
) -> RiskRadarEvalResult:
    chunks = await _fetch_chunks(tender_id, db_url)
    state = _build_state(tender_id, eval_run_id, "eval", chunks)
    config = {"configurable": {"thread_id": state["run_id"]}}
    result = await risk_radar_node(state, config)
    model_findings: list[dict] = result.get("risk_findings", [])

    total_labelled = len(labelled_findings)
    total_found = len(model_findings)
    total_matched, _, _ = match_findings(model_findings, labelled_findings)

    recall = total_matched / max(total_labelled, 1)
    if total_found > 0:
        precision_matched, _, _ = match_findings(labelled_findings, model_findings)
    else:
        precision_matched = 0
    precision = precision_matched / max(total_found, 1)
    f1 = 2 * recall * precision / max(recall + precision, 0.001)
    pass_fail = "PASS" if recall >= 0.85 else "FAIL"

    per_category: list[CategoryMetrics] = []
    for cat in CATEGORIES:
        labelled_in_cat = [f for f in labelled_findings if f.get("category") == cat]
        found_in_cat = [f for f in model_findings if f.get("category") == cat]
        matched_in_cat, _, _ = match_findings(found_in_cat, labelled_in_cat)

        cat_recall = matched_in_cat / max(len(labelled_in_cat), 1)
        cat_precision = matched_in_cat / max(len(found_in_cat), 1)
        per_category.append(CategoryMetrics(
            category=cat,
            recall=cat_recall,
            precision=cat_precision,
            labelled=len(labelled_in_cat),
            found=len(found_in_cat),
            matched=matched_in_cat,
        ))

    return RiskRadarEvalResult(
        recall=recall,
        precision=precision,
        f1=f1,
        total_labelled=total_labelled,
        total_found=total_found,
        total_matched=total_matched,
        per_category=per_category,
        pass_fail=pass_fail,
    )


async def run_scorer_consistency_eval(
    tender_id: str,
    company_id: str,
    eval_run_id: str,
    db_url: str,
) -> ScorerConsistencyResult:
    chunks = await _fetch_chunks(tender_id, db_url)
    scores: list[float] = []
    breakdown_list: list[dict] = []

    for i in range(3):
        run_id = f"{eval_run_id}-{i}"
        state = _build_state(tender_id, run_id, company_id, chunks)
        config = {"configurable": {"thread_id": state["run_id"]}}
        result = await feasibility_scorer_node(state, config)
        scores.append(result.get("feasibility_score", 0.0))
        breakdown_list.append(result.get("feasibility_breakdown") or {})

    mean = sum(scores) / 3
    std_dev = statistics.stdev(scores) if len(scores) >= 2 else 0.0
    pass_fail = "PASS" if std_dev <= 5.0 else "FAIL"

    all_dims: set[str] = set()
    for b in breakdown_list:
        if b:
            all_dims.update(b.keys())

    dimension_ranges: dict[str, list[float]] = {}
    for dim in sorted(all_dims):
        dim_scores: list[float] = []
        for b in breakdown_list:
            if b and dim in b:
                dim_entry = b[dim]
                if isinstance(dim_entry, dict):
                    dim_scores.append(dim_entry.get("score", 0.0))
                else:
                    dim_scores.append(float(dim_entry))
        if dim_scores:
            dimension_ranges[dim] = [min(dim_scores), max(dim_scores)]

    return ScorerConsistencyResult(
        scores=scores,
        mean=mean,
        std_dev=std_dev,
        pass_fail=pass_fail,
        dimension_ranges=dimension_ranges,
    )


def _print_text_report(result: EvalRunResult) -> None:
    sep = "=" * 46
    sub = "-" * 46

    print(sep)
    print("  TenderIQ Evaluation Report")
    print(f"  Tender: {result.tender_name}")
    print(f"  Run at: {result.run_at}")

    if result.risk_radar is not None:
        rr = result.risk_radar
        rr_status = "PASS" if rr.pass_fail == "PASS" else "FAIL"
        print(sub)
        print("  RISK RADAR")
        print(f"    Labelled clauses:  {rr.total_labelled}")
        print(f"    Model findings:    {rr.total_found}")
        print(f"    Matched:           {rr.total_matched}")
        print(f"    Recall:            {rr.recall:.1%}  (target: >=85%)")
        print(f"    Precision:         {rr.precision:.1%}")
        print(f"    F1:                {rr.f1:.1%}")
        print(f"    Status:            {rr_status}")
        print()
        print("    Per category:")
        for cm in rr.per_category:
            print(
                f"      {cm.category:<12} "
                f"{cm.recall:.1%} ({cm.matched}/{cm.labelled})"
            )

    if result.scorer is not None:
        sc = result.scorer
        sc_status = "PASS" if sc.pass_fail == "PASS" else "FAIL"
        print(sub)
        print("  FEASIBILITY SCORER CONSISTENCY")
        scores_str = ", ".join(f"{s:.1f}" for s in sc.scores)
        print(f"    Scores (3 runs):   [{scores_str}]")
        print(f"    Mean:              {sc.mean:.1f}")
        print(f"    Std deviation:     {sc.std_dev:.1f}  (target: <=5.0)")
        print(f"    Status:            {sc_status}")

        if sc.dimension_ranges:
            print()
            print("    Dimension ranges:")
            for dim, rng in sc.dimension_ranges.items():
                print(f"      {dim}: [{rng[0]:.1f}, {rng[1]:.1f}]")

    print(sub)
    print(f"  TOTAL COST:          ${result.total_cost_usd:.4f} USD")

    print(f"  OVERALL STATUS:      {result.overall_status}")
    if result.notes:
        print(f"  Notes: {result.notes}")
    print(sep)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="TenderIQ Evaluation Harness — REQ-012 Slice 1"
    )
    parser.add_argument(
        "--tender-id",
        required=True,
        help="UUID of the tender to evaluate against.",
    )
    parser.add_argument(
        "--company-id",
        default=None,
        help="UUID of company (required for --scorer).",
    )
    parser.add_argument(
        "--risk",
        action="store_true",
        help="Run risk radar recall/precision eval.",
    )
    parser.add_argument(
        "--scorer",
        action="store_true",
        help="Run scorer consistency eval (3 runs).",
    )
    parser.add_argument(
        "--output",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--ground-truth",
        default=None,
        help="Path to labelled_sample_tender.json (default: eval/labelled_sample_tender.json).",
    )
    args = parser.parse_args()

    if not args.risk and not args.scorer:
        print("Error: At least one eval type must be enabled (--risk or --scorer).",
              file=sys.stderr)
        return 1

    if args.scorer and not args.company_id:
        print("Error: --company-id is required when --scorer is enabled.",
              file=sys.stderr)
        return 1

    db_url = _get_db_url()

    gt_path = args.ground_truth or (
        Path(__file__).resolve().parent / "labelled_sample_tender.json"
    )
    ground_truth = _load_ground_truth(str(gt_path))
    labelled_findings: list[dict] = ground_truth.get("labelled_findings", [])

    eval_id = str(uuid.uuid4())
    eval_run_id = f"eval-{eval_id}"
    tender_name = await _fetch_tender_name(args.tender_id, db_url)

    risk_radar: RiskRadarEvalResult | None = None
    scorer: ScorerConsistencyResult | None = None
    cost_run_ids: list[str] = []
    notes: str | None = None

    if args.risk:
        if not labelled_findings:
            logger.info("WARNING: No labelled ground truth. Risk eval skipped.")
            notes = "No labelled ground truth available. Risk eval skipped."
        else:
            logger.info("Running Risk Radar eval...")
            cost_run_ids.append(eval_run_id)
            risk_radar = await run_risk_radar_eval(
                args.tender_id, labelled_findings, eval_run_id, db_url
            )
            logger.info("Risk Radar eval complete.")

    if args.scorer and args.company_id:
        logger.info("Running Scorer Consistency eval (3 runs)...")
        try:
            for i in range(3):
                cost_run_ids.append(f"{eval_run_id}-{i}")
            scorer = await run_scorer_consistency_eval(
                args.tender_id, args.company_id, eval_run_id, db_url
            )
            logger.info("Scorer Consistency eval complete.")
        except ValueError as exc:
            logger.warning("Scorer eval failed: %s", exc)
            notes = f"""Scorer eval failed: {exc}""" if not notes else f"""{notes} Scorer eval failed: {exc}"""

    total_cost_usd = await _fetch_total_cost(cost_run_ids, db_url)

    all_pass = True
    any_run = False
    if risk_radar is not None:
        any_run = True
        if risk_radar.pass_fail != "PASS":
            all_pass = False
    if scorer is not None:
        any_run = True
        if scorer.pass_fail != "PASS":
            all_pass = False

    if not any_run:
        overall_status = "NO_DATA"
    elif all_pass:
        overall_status = "PASS"
    elif risk_radar is None and scorer is not None:
        overall_status = "PARTIAL"
    else:
        overall_status = "FAIL"

    run_at = datetime.now(timezone.utc).isoformat()

    result = EvalRunResult(
        eval_id=eval_id,
        tender_id=args.tender_id,
        tender_name=tender_name,
        run_at=run_at,
        risk_radar=risk_radar,
        scorer=scorer,
        total_cost_usd=total_cost_usd,
        overall_status=overall_status,
        notes=notes,
    )

    if args.output == "json":
        print(result.model_dump_json(indent=2))
    else:
        _print_text_report(result)

    return 0 if overall_status == "PASS" else 1


if __name__ == "__main__":
    import asyncio

    exit_code = asyncio.run(main())
    sys.exit(exit_code)
