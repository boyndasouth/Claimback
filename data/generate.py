#!/usr/bin/env python3
"""
ClaimBack synthetic data generator.

Produces:
  - 250 denial letters (PDF)
  - 250 EOBs (PDF)
  - 250 ground-truth label records (cases.jsonl)
  - 8 insurance policy PDFs
  - 10 state-rule JSON files
  - CPT/ICD-10 subset JSON (fixtures/cpt_icd10.json, already present)

Usage:
    python data/generate.py --seed 42 --count 250

No watsonx calls — purely Faker + Jinja2 + weasyprint.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

# ---------------------------------------------------------------------------
# Ensure the repo root is on sys.path so we can import backend models
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from faker import Faker

try:
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML as WeasyprintHTML  # type: ignore[import]
    _PDF_AVAILABLE = True
except (ImportError, OSError):
    _PDF_AVAILABLE = False
    print(
        "WARNING: weasyprint native libs unavailable — writing HTML files only (no PDF).",
        file=sys.stderr,
    )
    # Provide a stub so the rest of the module loads cleanly
    from jinja2 import Environment, FileSystemLoader  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = pathlib.Path(__file__).resolve().parent
FIXTURES_DIR = DATA_DIR / "fixtures"
TEMPLATES_DIR = DATA_DIR / "templates"
GENERATED_DIR = DATA_DIR / "generated"

# ---------------------------------------------------------------------------
# Denial categories and codes matching §6
# ---------------------------------------------------------------------------
DENIAL_CATEGORIES = [
    "medical_necessity",
    "prior_authorization",
    "coding_error",
    "out_of_network",
    "not_covered_benefit",
    "administrative",
]

# CARC codes associated with each category
CARC_BY_CATEGORY: dict[str, list[str]] = {
    "medical_necessity": ["CO-50", "CO-57", "CO-151", "CO-167"],
    "prior_authorization": ["CO-15", "CO-197", "CO-198"],
    "coding_error": ["CO-4", "CO-5", "CO-6", "CO-11", "CO-16", "CO-18"],
    "out_of_network": ["CO-97", "CO-100", "CO-242"],
    "not_covered_benefit": ["CO-96", "CO-45", "CO-253"],
    "administrative": ["CO-29", "CO-125", "CO-170", "CO-180"],
}

DENIAL_REASON_TEXTS: dict[str, list[str]] = {
    "medical_necessity": [
        "The service does not meet the Plan's medical necessity criteria for the stated diagnosis.",
        "Clinical documentation does not support the medical necessity of the requested procedure.",
        "The requested service is not supported by nationally recognized clinical guidelines for the submitted diagnosis.",
        "Based on the information submitted, this service is not considered medically necessary under Plan guidelines.",
    ],
    "prior_authorization": [
        "This service required prior authorization which was not obtained before the service was rendered.",
        "No prior authorization on file for the submitted procedure code.",
        "The service was rendered without required pre-service approval from the Plan.",
        "Prior authorization was denied; service performed without an approved authorization.",
    ],
    "coding_error": [
        "The submitted procedure code is not compatible with the submitted diagnosis code.",
        "Modifier is missing or invalid for the submitted procedure.",
        "Duplicate claim or service already adjudicated for the same date of service.",
        "The procedure code submitted is not a covered service under the submitted revenue code.",
    ],
    "out_of_network": [
        "The rendering provider is not contracted with this Plan.",
        "Services rendered at a facility outside the Plan's network without prior approval.",
        "Out-of-network provider — member did not obtain required referral for out-of-network care.",
        "The facility where services were rendered is not an approved in-network facility.",
    ],
    "not_covered_benefit": [
        "This service is specifically excluded from coverage under the member's benefit plan.",
        "The requested service is not a covered benefit under the member's current plan.",
        "Coverage for this service has been exhausted for the current benefit period.",
        "This service falls under a plan exclusion as described in the Evidence of Coverage.",
    ],
    "administrative": [
        "Claim was received after the timely filing deadline.",
        "Required documentation was not submitted with the claim.",
        "Missing or invalid member identification information on the submitted claim.",
        "Claim form is incomplete — required fields are missing or illegible.",
    ],
}

CPT_DESCRIPTIONS: dict[str, str] = {
    "70553": "MRI Brain with and without contrast",
    "71046": "X-ray chest 2 views",
    "72148": "MRI lumbar spine without contrast",
    "73221": "MRI joint upper extremity without contrast",
    "73721": "MRI joint lower extremity without contrast",
    "74177": "CT abdomen and pelvis with contrast",
    "75571": "CT heart calcium scoring",
    "76817": "Ultrasound obstetric transvaginal",
    "77067": "Screening mammography bilateral",
    "93306": "Echocardiography complete with spectral Doppler",
    "90837": "Psychotherapy 60 minutes",
    "97161": "Physical therapy evaluation low complexity",
    "97110": "Therapeutic exercises",
    "99214": "Office/outpatient visit established moderate complexity",
    "99213": "Office/outpatient visit established low complexity",
    "85025": "Complete blood count with differential",
    "80053": "Comprehensive metabolic panel",
    "92012": "Ophthalmological services established patient",
}


def _load_fixture(name: str) -> object:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


def _render_pdf(html_str: str, out_path: pathlib.Path) -> None:
    """Render HTML string to PDF using weasyprint, or write HTML fallback."""
    if _PDF_AVAILABLE:
        WeasyprintHTML(string=html_str).write_pdf(str(out_path))
    else:
        out_path.with_suffix(".html").write_text(html_str, encoding="utf-8")


def _build_case(
    idx: int,
    faker: Faker,
    rng: random.Random,
    cpt_codes_pool: list[str],
    icd10_codes_pool: list[str],
    valid_pairs: list[list[str]],
    state_rules: list[dict],
    payers_pool: list[dict],
) -> dict:
    """Build a single synthetic case record."""
    case_id = str(uuid.UUID(int=rng.getrandbits(128)))

    # Pick state and payer
    state_rule = rng.choice(state_rules)
    payer_def = rng.choice(payers_pool)

    # Difficulty flags (§9)
    has_coding_error = rng.random() < 0.20
    missing_denial_code = rng.random() < 0.15
    low_quality = rng.random() < 0.10
    expedited_eligible = rng.random() < 0.10

    # Pick denial category
    if has_coding_error:
        category = "coding_error"
    else:
        category = rng.choice([c for c in DENIAL_CATEGORIES if c != "coding_error"])

    # Dates
    service_date = faker.date_between(start_date="-2y", end_date="-10d")
    denial_date = service_date + timedelta(days=rng.randint(14, 45))

    # Deadlines
    internal_days = state_rule["internal_days"]
    internal_deadline = denial_date + timedelta(days=internal_days)
    ext_available = state_rule["external_review_available"]
    ext_deadline = None
    if ext_available and state_rule.get("external_days"):
        ext_deadline = denial_date + timedelta(days=state_rule["external_days"] + internal_days)
    expedited_days = state_rule["expedited_days"]

    # Pick codes
    num_cpt = rng.randint(1, 3)
    if has_coding_error:
        # Pick CPT codes that are NOT in the valid pairs to inject an invalid combination
        valid_cpts = {p[0] for p in valid_pairs}
        base_cpt = rng.choice(list(valid_cpts))
        cpt_selected = [base_cpt]
        # Pick a random ICD-10 that is not in the valid pairs for this CPT
        valid_icd_for_cpt = {p[1] for p in valid_pairs if p[0] == base_cpt}
        invalid_icds = [c for c in icd10_codes_pool if c not in valid_icd_for_cpt]
        icd_selected = [rng.choice(invalid_icds)] if invalid_icds else [rng.choice(icd10_codes_pool)]
    else:
        # Pick from valid pairs to get a coherent cpt/icd10 combination
        pair = rng.choice(valid_pairs)
        cpt_selected = [pair[0]]
        icd_selected = [pair[1]]
        # Optionally add extra CPT codes
        for _ in range(num_cpt - 1):
            cpt_selected.append(rng.choice(cpt_codes_pool))

    # Billed amount
    billed = Decimal(str(round(rng.uniform(150, 8500), 2)))
    allowed = round(float(billed) * rng.uniform(0.4, 0.85), 2)
    deductible = round(min(float(billed) * rng.uniform(0.1, 0.4), 2500), 2)
    coinsurance = round(max(float(billed) - deductible - 50, 0) * 0.2, 2)
    plan_paid = 0.0
    member_owes = round(float(billed), 2)

    # CARC code
    carc_pool = CARC_BY_CATEGORY.get(category, ["CO-50"])
    denial_reason_code = None if missing_denial_code else rng.choice(carc_pool)
    denial_reason_text = rng.choice(DENIAL_REASON_TEXTS[category])

    # Patient / provider info (Faker)
    patient_name = faker.name()
    member_id = "MBR" + faker.numerify("########")
    group_number = faker.numerify("######")
    provider_name = "Dr. " + faker.name()
    facility_name = faker.company() + " Medical Center"
    npi = faker.numerify("##########")
    payer_address = faker.address().replace("\n", ", ")
    patient_address = faker.address().replace("\n", ", ")
    appeals_address = faker.address().replace("\n", ", ")
    reviewer_name = "Dr. " + faker.name()

    # Synthetic outcome for Success Scorer training (rule-based simulator)
    # Rules: coding errors + complete evidence = higher success; admin denials harder to win
    base_prob = {
        "medical_necessity": 0.55,
        "prior_authorization": 0.45,
        "coding_error": 0.70,
        "out_of_network": 0.30,
        "not_covered_benefit": 0.20,
        "administrative": 0.40,
    }[category]
    if has_coding_error:
        base_prob += 0.10
    if expedited_eligible:
        base_prob += 0.05
    if missing_denial_code:
        base_prob -= 0.05
    synthetic_outcome = 1 if rng.random() < min(max(base_prob, 0.05), 0.95) else 0

    # EOB line items
    eob_lines = []
    for cpt in cpt_selected:
        line_billed = round(float(billed) / len(cpt_selected), 2)
        eob_lines.append({
            "cpt": cpt,
            "icd10": icd_selected[0] if icd_selected else "",
            "billed": line_billed,
            "allowed": round(line_billed * 0.65, 2),
            "deductible": round(min(line_billed * 0.3, 1000), 2),
            "coinsurance": 0.0,
            "plan_paid": 0.0,
            "member_owes": line_billed,
            "status": "DENIED",
        })

    # Build the full case dict
    plan_year = denial_date.year
    effective_date = date(plan_year, 1, 1)
    termination_date = date(plan_year, 12, 31)

    case = {
        # ExtractedClaim fields
        "claim_id": f"CLM-{case_id[:8].upper()}",
        "case_id": case_id,
        "payer": payer_def["payer"],
        "plan_type": payer_def["plan_type"],
        "plan_name": payer_def["plan_name"],
        "state": state_rule["state"],
        "service_date": str(service_date),
        "denial_date": str(denial_date),
        "cpt_codes": cpt_selected,
        "icd10_codes": icd_selected,
        "billed_amount": str(billed),
        "denial_reason_text": denial_reason_text,
        "denial_reason_code": denial_reason_code,
        "field_confidence": {
            "claim_id": round(rng.uniform(0.92, 1.0), 3),
            "payer": round(rng.uniform(0.90, 1.0), 3),
            "service_date": round(rng.uniform(0.88, 1.0), 3),
            "denial_date": round(rng.uniform(0.88, 1.0), 3),
            "cpt_codes": round(rng.uniform(0.80, 1.0), 3),
            "icd10_codes": round(rng.uniform(0.78, 1.0), 3),
            "billed_amount": round(rng.uniform(0.85, 1.0), 3),
        },
        # Ground-truth labels
        "gt_category": category,
        "gt_has_coding_error": has_coding_error,
        "gt_missing_denial_code": missing_denial_code,
        "gt_low_quality": low_quality,
        "gt_expedited_eligible": expedited_eligible,
        "gt_internal_appeal_deadline": str(internal_deadline),
        "gt_external_review_available": ext_available,
        "gt_external_review_deadline": str(ext_deadline) if ext_deadline else None,
        "gt_synthetic_outcome": synthetic_outcome,
        "split": "eval" if idx < 200 else "held_out",
        # Template rendering extras (not part of ExtractedClaim)
        "member_id": member_id,
        "group_number": group_number,
        "patient_name": patient_name,
        "patient_address": patient_address,
        "provider_name": provider_name,
        "facility_name": facility_name,
        "npi": npi,
        "payer_address": payer_address,
        "appeals_address": appeals_address,
        "reviewer_name": reviewer_name,
        "internal_appeal_days": internal_days,
        "internal_appeal_deadline": str(internal_deadline),
        "external_review_available": ext_available,
        "external_review_deadline": str(ext_deadline) if ext_deadline else None,
        "expedited_eligible": expedited_eligible,
        "expedited_days": expedited_days,
        "cpt_descriptions": CPT_DESCRIPTIONS,
        "eob_lines": eob_lines,
        "total_allowed": round(allowed, 2),
        "total_deductible": round(deductible, 2),
        "total_coinsurance": round(coinsurance, 2),
        "total_member_owes": round(float(billed), 2),
        "deductible_met": round(rng.uniform(0, deductible), 2),
        "deductible_total": round(deductible * 3, 2),
        "oop_met": round(rng.uniform(0, float(billed) * 0.5), 2),
        "oop_max": round(float(billed) * rng.uniform(1.5, 3.0), 2),
        # Additional rendering flags
        "low_quality": low_quality,
        "has_coding_error": has_coding_error,
    }
    return case


def _build_policy(
    payer_def: dict,
    rng: random.Random,
    plan_year: int = 2025,
) -> dict:
    """Build a synthetic policy context dict for rendering."""
    state_abbr = rng.choice(["CA", "TX", "NY", "FL", "IL", "PA", "OH", "GA", "NC", "WA"])
    individual_deductible = rng.choice([500, 750, 1000, 1500, 2000, 2800, 3000, 6500])
    plan_type = payer_def["plan_type"]
    oop_max = individual_deductible * rng.choice([2, 3, 4])
    coinsurance = rng.choice([10, 20, 30])
    return {
        "doc_id": str(uuid.UUID(int=rng.getrandbits(128)))[:8].upper(),
        "payer": payer_def["payer"],
        "plan_name": payer_def["plan_name"],
        "plan_type": plan_type,
        "coverage_description": payer_def["coverage_description"],
        "state": state_abbr,
        "plan_year": str(plan_year),
        "effective_date": f"January 1, {plan_year}",
        "termination_date": f"December 31, {plan_year}",
        "group_id": "".join([str(rng.randint(0, 9)) for _ in range(6)]),
        "individual_deductible": individual_deductible,
        "oop_max": oop_max,
        "coinsurance": coinsurance,
        "pcp_copay": rng.choice([10, 15, 20, 25, 30]),
        "specialist_copay": rng.choice([30, 40, 50, 60]),
        "er_copay": rng.choice([100, 150, 200, 250, 300]),
        "requires_referral": plan_type != "employer" or rng.random() < 0.4,
        "timely_filing_days": rng.choice([90, 120, 180, 365]),
        "internal_appeal_days": rng.choice([60, 90, 120, 180]),
        "external_review_days": rng.choice([30, 45, 60]),
    }


def generate(seed: int, count: int) -> None:
    """Main generation entrypoint."""
    rng = random.Random(seed)
    faker = Faker()
    Faker.seed(seed)

    # Load fixtures
    cpt_data = _load_fixture("cpt_icd10.json")
    cpt_codes_pool: list[str] = cpt_data["cpt_codes"]  # type: ignore[index]
    icd10_codes_pool: list[str] = cpt_data["icd10_codes"]  # type: ignore[index]
    valid_pairs: list[list[str]] = cpt_data["valid_pairs"]  # type: ignore[index]
    state_rules: list[dict] = _load_fixture("state_rules.json")  # type: ignore[assignment]
    payers_pool: list[dict] = _load_fixture("payers.json")  # type: ignore[assignment]

    # Set up Jinja2
    jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    denial_tmpl = jinja_env.get_template("denial_letter.html")
    eob_tmpl = jinja_env.get_template("eob.html")
    policy_tmpl = jinja_env.get_template("policy.html")

    # Create output directories
    denial_dir = GENERATED_DIR / "denial_letters"
    eob_dir = GENERATED_DIR / "eobs"
    policy_dir = GENERATED_DIR / "policies"
    state_dir = GENERATED_DIR / "state_rules"
    for d in [denial_dir, eob_dir, policy_dir, state_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # ── Generate state rule files ───────────────────────────────────────────
    for rule in state_rules:
        out = state_dir / f"{rule['state'].lower()}_rules.json"
        out.write_text(json.dumps(rule, indent=2), encoding="utf-8")
    print(f"  State rule files: {len(state_rules)}")

    # ── Generate 8 policy PDFs ──────────────────────────────────────────────
    for payer_def in payers_pool:
        policy = _build_policy(payer_def, rng)
        policy_html = policy_tmpl.render(policy=policy)
        slug = (
            payer_def["payer"].lower().replace(" ", "_")
            + "_"
            + payer_def["plan_type"]
        )
        out_path = policy_dir / f"{slug}.pdf"
        _render_pdf(policy_html, out_path)
    print(f"  Policy PDFs: {len(payers_pool)}")

    # ── Generate 250 cases ──────────────────────────────────────────────────
    cases = []
    for i in range(count):
        case = _build_case(
            i, faker, rng, cpt_codes_pool, icd10_codes_pool,
            valid_pairs, state_rules, payers_pool,
        )
        cases.append(case)

        # Denial letter PDF
        denial_html = denial_tmpl.render(case=case)
        _render_pdf(denial_html, denial_dir / f"{case['case_id']}.pdf")

        # EOB PDF
        eob_html = eob_tmpl.render(case=case)
        _render_pdf(eob_html, eob_dir / f"{case['case_id']}.pdf")

        if (i + 1) % 50 == 0:
            print(f"  Generated {i + 1}/{count} cases...")

    # ── Write ground-truth JSONL ────────────────────────────────────────────
    # Strip rendering-only keys before writing (keep only extractable + gt fields)
    _RENDER_ONLY = {
        "member_id", "group_number", "patient_name", "patient_address",
        "provider_name", "facility_name", "npi", "payer_address",
        "appeals_address", "reviewer_name", "internal_appeal_days",
        "internal_appeal_deadline", "external_review_available",
        "external_review_deadline", "expedited_eligible", "expedited_days",
        "cpt_descriptions", "eob_lines", "total_allowed", "total_deductible",
        "total_coinsurance", "total_member_owes", "deductible_met",
        "deductible_total", "oop_met", "oop_max", "low_quality",
        "has_coding_error", "plan_name",
    }
    jsonl_path = GENERATED_DIR / "cases.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for case in cases:
            record = {k: v for k, v in case.items() if k not in _RENDER_ONLY}
            f.write(json.dumps(record) + "\n")

    # ── Summary ─────────────────────────────────────────────────────────────
    n_coding = sum(1 for c in cases if c["gt_has_coding_error"])
    n_missing = sum(1 for c in cases if c["gt_missing_denial_code"])
    n_low = sum(1 for c in cases if c["gt_low_quality"])
    n_exp = sum(1 for c in cases if c["gt_expedited_eligible"])
    n_eval = sum(1 for c in cases if c["split"] == "eval")
    n_held = sum(1 for c in cases if c["split"] == "held_out")

    ext = "pdf" if _PDF_AVAILABLE else "html"
    print(f"\n✓ Generated {count} cases at seed={seed}")
    print(f"  denial_letters/  : {count} .{ext} files → {denial_dir}")
    print(f"  eobs/            : {count} .{ext} files → {eob_dir}")
    print(f"  policies/        : {len(payers_pool)} .{ext} files → {policy_dir}")
    print(f"  state_rules/     : {len(state_rules)} .json files → {state_dir}")
    print(f"  cases.jsonl      : {jsonl_path}")
    print("\nDifficulty injections:")
    print(f"  coding errors    : {n_coding}/{count} ({100*n_coding/count:.0f}%)")
    print(f"  missing code     : {n_missing}/{count} ({100*n_missing/count:.0f}%)")
    print(f"  low quality scan : {n_low}/{count} ({100*n_low/count:.0f}%)")
    print(f"  expedited elig.  : {n_exp}/{count} ({100*n_exp/count:.0f}%)")
    print("\nSplits:")
    print(f"  eval             : {n_eval}")
    print(f"  held_out         : {n_held}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ClaimBack synthetic dataset.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--count", type=int, default=250, help="Number of cases (default: 250)")
    args = parser.parse_args()
    # Windows consoles default to cp1252, which can't print ✓ / → in the summary.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    generate(seed=args.seed, count=args.count)


if __name__ == "__main__":
    main()
