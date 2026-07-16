"""Shared Pydantic schemas for TenderIQ evaluation results (REQ-012).

Used by both the CLI (eval/run_eval.py) and the API endpoint
(app/api/routers/eval.py — Slice 2).
"""
from __future__ import annotations

from pydantic import BaseModel


class CategoryMetrics(BaseModel):
    category: str
    recall: float
    precision: float
    labelled: int
    found: int
    matched: int


class RiskRadarEvalResult(BaseModel):
    recall: float
    precision: float
    f1: float
    total_labelled: int
    total_found: int
    total_matched: int
    per_category: list[CategoryMetrics]
    pass_fail: str  # "PASS" if recall >= 0.85


class ScorerConsistencyResult(BaseModel):
    scores: list[float]  # 3 runs
    mean: float
    std_dev: float
    pass_fail: str  # "PASS" if std_dev <= 5.0
    dimension_ranges: dict  # {dim_name: [min, max]}


class EvalRunResult(BaseModel):
    eval_id: str
    tender_id: str
    tender_name: str
    run_at: str
    risk_radar: RiskRadarEvalResult | None
    scorer: ScorerConsistencyResult | None
    total_cost_usd: float
    overall_status: str  # "PASS"|"FAIL"|"PARTIAL"|"NO_DATA"
    notes: str | None
