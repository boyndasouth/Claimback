"""Unit tests for the synthetic data generator (§9)."""
from __future__ import annotations

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

# Import generator module
_DATA_DIR = _REPO_ROOT / "data"
sys.path.insert(0, str(_DATA_DIR))

import generate as gen  # type: ignore[import]
from app.models import ExtractedClaim

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

class TestFixtures:
    def test_cpt_icd10_fixture_loads(self) -> None:
        data = gen._load_fixture("cpt_icd10.json")
        assert isinstance(data, dict)
        assert len(data["cpt_codes"]) >= 200
        assert len(data["icd10_codes"]) >= 50
        assert len(data["valid_pairs"]) >= 10

    def test_state_rules_fixture(self) -> None:
        rules = gen._load_fixture("state_rules.json")
        assert isinstance(rules, list)
        assert len(rules) == 10
        for r in rules:
            assert "state" in r
            assert "internal_days" in r
            assert "external_review_available" in r
            assert r["internal_days"] >= 30

    def test_payers_fixture(self) -> None:
        payers = gen._load_fixture("payers.json")
        assert isinstance(payers, list)
        assert len(payers) == 8  # 4 payers × 2 plan types
        for p in payers:
            assert "payer" in p
            assert p["plan_type"] in ("employer", "marketplace")


# ---------------------------------------------------------------------------
# Case generation (single case, no file I/O)
# ---------------------------------------------------------------------------

class TestBuildCase:
    def _get_data(self):
        import random

        from faker import Faker
        rng = random.Random(42)
        faker = Faker()
        Faker.seed(42)
        cpt_data = gen._load_fixture("cpt_icd10.json")
        return (
            rng, faker,
            cpt_data["cpt_codes"],
            cpt_data["icd10_codes"],
            cpt_data["valid_pairs"],
            gen._load_fixture("state_rules.json"),
            gen._load_fixture("payers.json"),
        )

    def test_case_has_all_extracted_claim_fields(self) -> None:
        rng, faker, cpts, icds, pairs, rules, payers = self._get_data()
        case = gen._build_case(0, faker, rng, cpts, icds, pairs, rules, payers)
        # Must be able to construct an ExtractedClaim from the case dict
        ec = ExtractedClaim(
            claim_id=case["claim_id"],
            payer=case["payer"],
            plan_type=case["plan_type"],
            state=case["state"],
            service_date=case["service_date"],
            denial_date=case["denial_date"],
            cpt_codes=case["cpt_codes"],
            icd10_codes=case["icd10_codes"],
            billed_amount=case["billed_amount"],
            denial_reason_text=case["denial_reason_text"],
            denial_reason_code=case["denial_reason_code"],
            field_confidence=case["field_confidence"],
        )
        assert ec.claim_id.startswith("CLM-")

    def test_case_has_ground_truth_fields(self) -> None:
        rng, faker, cpts, icds, pairs, rules, payers = self._get_data()
        case = gen._build_case(0, faker, rng, cpts, icds, pairs, rules, payers)
        for f in ("gt_category", "gt_has_coding_error", "gt_missing_denial_code",
                  "gt_low_quality", "gt_expedited_eligible",
                  "gt_internal_appeal_deadline", "gt_synthetic_outcome", "split"):
            assert f in case, f"Missing ground-truth field: {f}"

    def test_split_eval_for_first_200(self) -> None:
        rng, faker, cpts, icds, pairs, rules, payers = self._get_data()
        for i in range(200):
            case = gen._build_case(i, faker, rng, cpts, icds, pairs, rules, payers)
            assert case["split"] == "eval"

    def test_split_held_out_for_last_50(self) -> None:
        rng, faker, cpts, icds, pairs, rules, payers = self._get_data()
        for i in range(200, 250):
            case = gen._build_case(i, faker, rng, cpts, icds, pairs, rules, payers)
            assert case["split"] == "held_out"

    def test_coding_error_uses_invalid_cpt_icd10_pair(self) -> None:
        """When coding_error=True the CPT/ICD-10 pair must NOT be in valid_pairs."""
        import random

        from faker import Faker

        # Force a coding error by patching rng to always return < 0.20
        rng_base = random.Random(42)
        faker = Faker()
        Faker.seed(42)
        cpt_data = gen._load_fixture("cpt_icd10.json")
        pairs = cpt_data["valid_pairs"]
        pair_set = {(p[0], p[1]) for p in pairs}

        found_coding_error = False
        for _ in range(100):
            rng = random.Random(rng_base.randint(0, 2**32))
            case = gen._build_case(
                0, faker, rng,
                cpt_data["cpt_codes"], cpt_data["icd10_codes"],
                pairs,
                gen._load_fixture("state_rules.json"),
                gen._load_fixture("payers.json"),
            )
            if case["gt_has_coding_error"]:
                found_coding_error = True
                # Verify the combination is invalid
                cpt = case["cpt_codes"][0]
                icd = case["icd10_codes"][0]
                assert (cpt, icd) not in pair_set, (
                    f"Expected invalid pair but got valid: ({cpt}, {icd})"
                )
                break
        assert found_coding_error, "Could not generate a coding-error case in 100 tries"

    def test_billed_amount_in_range(self) -> None:
        rng, faker, cpts, icds, pairs, rules, payers = self._get_data()
        for _ in range(20):
            case = gen._build_case(0, faker, rng, cpts, icds, pairs, rules, payers)
            amt = float(case["billed_amount"])
            assert 150 <= amt <= 8500, f"billed_amount out of range: {amt}"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_same_output(self) -> None:
        """Running generate() twice with the same seed must produce identical cases.jsonl."""
        import random

        from faker import Faker

        # Use in-memory generation (no file writes) by calling _build_case deterministically
        def _run(seed: int) -> list[dict]:
            rng = random.Random(seed)
            faker = Faker()
            Faker.seed(seed)
            cpt_data = gen._load_fixture("cpt_icd10.json")
            state_rules = gen._load_fixture("state_rules.json")
            payers = gen._load_fixture("payers.json")
            cases = []
            for i in range(10):
                case = gen._build_case(
                    i, faker, rng,
                    cpt_data["cpt_codes"], cpt_data["icd10_codes"],
                    cpt_data["valid_pairs"], state_rules, payers,
                )
                cases.append({
                    "claim_id": case["claim_id"],
                    "payer": case["payer"],
                    "gt_category": case["gt_category"],
                    "split": case["split"],
                })
            return cases

        run1 = _run(42)
        run2 = _run(42)
        assert run1 == run2

    def test_different_seeds_differ(self) -> None:
        import random

        from faker import Faker

        def _first_claim_id(seed: int) -> str:
            rng = random.Random(seed)
            faker = Faker()
            Faker.seed(seed)
            cpt_data = gen._load_fixture("cpt_icd10.json")
            case = gen._build_case(
                0, faker, rng,
                cpt_data["cpt_codes"], cpt_data["icd10_codes"],
                cpt_data["valid_pairs"],
                gen._load_fixture("state_rules.json"),
                gen._load_fixture("payers.json"),
            )
            return case["claim_id"]

        assert _first_claim_id(42) != _first_claim_id(99)


# ---------------------------------------------------------------------------
# Difficulty injection percentages (statistical)
# ---------------------------------------------------------------------------

class TestDifficultyPercentages:
    """Check that over 250 cases, injected percentages are approximately correct."""

    def _generate_cases(self, seed: int = 42, count: int = 250) -> list[dict]:
        import random

        from faker import Faker
        rng = random.Random(seed)
        faker = Faker()
        Faker.seed(seed)
        cpt_data = gen._load_fixture("cpt_icd10.json")
        state_rules = gen._load_fixture("state_rules.json")
        payers = gen._load_fixture("payers.json")
        return [
            gen._build_case(
                i, faker, rng,
                cpt_data["cpt_codes"], cpt_data["icd10_codes"],
                cpt_data["valid_pairs"], state_rules, payers,
            )
            for i in range(count)
        ]

    def test_coding_error_rate_approx_20pct(self) -> None:
        cases = self._generate_cases()
        rate = sum(1 for c in cases if c["gt_has_coding_error"]) / len(cases)
        # Allow ±8% tolerance around 20%
        assert 0.12 <= rate <= 0.28, f"Coding error rate {rate:.2%} outside 12–28%"

    def test_missing_denial_code_rate_approx_15pct(self) -> None:
        cases = self._generate_cases()
        rate = sum(1 for c in cases if c["gt_missing_denial_code"]) / len(cases)
        assert 0.07 <= rate <= 0.23, f"Missing denial code rate {rate:.2%} outside 7–23%"

    def test_low_quality_rate_approx_10pct(self) -> None:
        cases = self._generate_cases()
        rate = sum(1 for c in cases if c["gt_low_quality"]) / len(cases)
        assert 0.04 <= rate <= 0.18, f"Low quality rate {rate:.2%} outside 4–18%"

    def test_expedited_rate_approx_10pct(self) -> None:
        cases = self._generate_cases()
        rate = sum(1 for c in cases if c["gt_expedited_eligible"]) / len(cases)
        assert 0.04 <= rate <= 0.18, f"Expedited rate {rate:.2%} outside 4–18%"

    def test_split_counts(self) -> None:
        cases = self._generate_cases()
        n_eval = sum(1 for c in cases if c["split"] == "eval")
        n_held = sum(1 for c in cases if c["split"] == "held_out")
        assert n_eval == 200
        assert n_held == 50


# ---------------------------------------------------------------------------
# Policy builder
# ---------------------------------------------------------------------------

class TestBuildPolicy:
    def test_policy_has_required_keys(self) -> None:
        import random
        rng = random.Random(42)
        payers = gen._load_fixture("payers.json")
        policy = gen._build_policy(payers[0], rng)
        for key in ("payer", "plan_name", "plan_type", "individual_deductible",
                    "oop_max", "coinsurance", "pcp_copay", "specialist_copay"):
            assert key in policy, f"Missing key: {key}"

    def test_oop_max_gte_deductible(self) -> None:
        import random
        rng = random.Random(42)
        payers = gen._load_fixture("payers.json")
        for p in payers:
            policy = gen._build_policy(p, rng)
            assert policy["oop_max"] >= policy["individual_deductible"]
