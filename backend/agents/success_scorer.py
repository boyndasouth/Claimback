"""Agent 12 — Success Scorer (§5).

Predicts appeal success probability using a LightGBM model trained on
synthetic outcomes (SPEC §9), then calls Granite to produce a plain-language
explanation.  Falls back to ``probability=0.5`` when the model file is absent.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models import (
    AgentResult,
    CaseContext,
    SourceRef,
    SuccessEstimate,
    SuccessScorerInput,
)
from services.tracing import trace
from services.watsonx import get_client

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_JINJA_ENV = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), autoescape=False)

_MODEL_PATH = Path(__file__).parent.parent / "models" / "success_scorer.pkl"

# ── Feature ordering must match train_scorer.py ───────────────────────────────
_CATEGORIES = [
    "medical_necessity",
    "prior_authorization",
    "coding_error",
    "out_of_network",
    "not_covered_benefit",
    "administrative",
]
_PLAN_TYPES = ["employer", "marketplace", "medicaid", "medicare_advantage"]
_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]

# ── Load model at import time (graceful fallback) ─────────────────────────────
_booster = None
_feature_names: list[str] = []

try:
    import joblib  # type: ignore[import]

    if _MODEL_PATH.exists():
        _booster = joblib.load(_MODEL_PATH)
        # Try to retrieve feature names; depends on sklearn Pipeline vs raw LGB
        if hasattr(_booster, "feature_names_in_"):
            _feature_names = list(_booster.feature_names_in_)
        elif hasattr(_booster, "feature_name"):
            # native lightgbm.Booster
            _feature_names = list(_booster.feature_name())
        logger.info("SuccessScorer: loaded model from %s", _MODEL_PATH)
    else:
        logger.warning("SuccessScorer: model file not found at %s — using fallback probability 0.5", _MODEL_PATH)
except Exception as _load_exc:
    logger.warning("SuccessScorer: could not load model (%s) — using fallback probability 0.5", _load_exc)


def _build_feature_vector(inp: SuccessScorerInput) -> list[float]:
    """One-hot encode + numeric features.  Order must match train_scorer.py."""
    vec: list[float] = []
    # Category one-hot
    for cat in _CATEGORIES:
        vec.append(1.0 if inp.category.value == cat else 0.0)
    # Plan type one-hot
    for pt in _PLAN_TYPES:
        vec.append(1.0 if inp.plan_type == pt else 0.0)
    # State one-hot
    for st in _STATES:
        vec.append(1.0 if inp.state.upper() == st else 0.0)
    # Numeric
    vec.append(float(inp.billed_amount))
    vec.append(float(inp.code_issues_count))
    vec.append(1.0 if inp.expedited_eligible else 0.0)
    return vec


def _build_feature_names() -> list[str]:
    names: list[str] = []
    names += [f"cat_{c}" for c in _CATEGORIES]
    names += [f"plan_{p}" for p in _PLAN_TYPES]
    names += [f"state_{s}" for s in _STATES]
    names += ["billed_amount", "code_issues_count", "expedited_eligible"]
    return names


class SuccessScorer:
    """Agent 12 — Success Scorer.

    Uses a LightGBM binary classifier to predict success probability, then
    calls Granite to generate a plain-language explanation.
    """

    name = "success_scorer"

    @trace(prompt_version="v1")
    async def run(
        self,
        input: SuccessScorerInput,  # noqa: A002
        ctx: CaseContext,
    ) -> AgentResult[SuccessEstimate]:
        """Score appeal success probability for *input*.

        Parameters
        ----------
        input:
            Feature bundle extracted from the case.
        ctx:
            Ambient case context.
        """
        start = time.monotonic()

        # ── Predict probability ───────────────────────────────────────────────
        if _booster is not None:
            import numpy as np  # type: ignore[import]

            fvec = _build_feature_vector(input)
            X = np.asarray([fvec], dtype=np.float32)
            if hasattr(_booster, "predict_proba"):
                # sklearn-style model (LGBMClassifier / Pipeline)
                prob = float(_booster.predict_proba(X)[0][1])
            else:
                # Native lightgbm.Booster from lgb.train(objective="binary"),
                # which is what evals/train_scorer.py saves: predict() already
                # returns P(class=1).
                prob = float(_booster.predict(X)[0])
            prob = min(max(prob, 0.0), 1.0)
            # Top-3 factors FOR THIS CASE: per-feature contributions (SHAP values,
            # in log-odds) from pred_contrib. Global importances were misleading —
            # e.g. "coding error" ranked top even on a medical-necessity case.
            # Positive = pushes toward success, negative = against.
            try:
                contrib = _booster.predict(X, pred_contrib=True)[0]
                feat_names = _feature_names or _build_feature_names()
                paired = [
                    (name, float(c))
                    for name, c, v in zip(feat_names, contrib[:-1], fvec)  # last = bias
                    # one-hot columns that are 0 for this case aren't "factors" of it
                    if v != 0.0 or not name.startswith(("cat_", "plan_", "state_"))
                ]
                paired.sort(key=lambda x: abs(x[1]), reverse=True)
                top_factors = [(name, round(c, 4)) for name, c in paired[:3]]
            except Exception as exc:  # noqa: BLE001
                logger.warning("SuccessScorer: could not compute contributions: %s", exc)
                top_factors = []
        else:
            prob = 0.5
            top_factors = []

        # ── Granite explanation ───────────────────────────────────────────────
        prob_pct = round(prob * 100, 1)
        prob_label = (
            "High" if prob >= 0.65 else ("Moderate" if prob >= 0.40 else "Low")
        )
        template = _JINJA_ENV.get_template("success_scorer/v1.md")
        raw_template = template.render(
            category=input.category.value,
            billed_amount=f"{input.billed_amount:,.2f}",
            state=input.state,
            plan_type=input.plan_type,
            code_issues_count=input.code_issues_count,
            expedited_eligible=input.expedited_eligible,
            probability_pct=prob_pct,
            probability_label=prob_label,
            top_factors=top_factors,
        )
        prompt = re.sub(r"^---\n.*?\n---\n", "", raw_template, flags=re.DOTALL).strip()

        client = get_client()
        generated_text, tokens_in, tokens_out = await client.generate(
            model_tier="routing",
            prompt=prompt,
            max_new_tokens=256,
        )
        explanation = generated_text.strip()

        latency_ms = int((time.monotonic() - start) * 1000)

        return AgentResult[SuccessEstimate](
            output=SuccessEstimate(
                probability=prob,
                top_factors=top_factors,
                explanation=explanation,
            ),
            confidence=prob,
            sources=[],
            trace_id=ctx.trace_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
