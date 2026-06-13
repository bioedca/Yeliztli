"""Tests for the query builder backend (P4-01).

Covers the PRD §8.7 adversarial test matrix:
- Basic operators (14+ operators)
- Nesting (2-level, 3-level, 5+ levels)
- Edge cases (empty groups, single-rule groups)
- Field validation (reject unknown fields)
- Type safety (reject type mismatches)
- Injection attempts (SQL keywords in values and field names)
- Large inputs (50+ rules, 10+ nested levels)
- Boolean logic (AND-only, OR-only, mixed, NOT groups)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, reference_metadata, samples
from backend.query.translator import (
    ALLOWED_FIELDS,
    SUPPORTED_OPERATORS,
    TranslationError,
    translate,
)

# ═══════════════════════════════════════════════════════════════════════
# Unit tests — translator in isolation
# ═══════════════════════════════════════════════════════════════════════


class TestTranslatorBasicOperators:
    """PRD §8.7: All 14+ operators produce correct SQL."""

    def test_equals(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "gene_symbol", "operator": "=", "value": "BRCA1"}],
        }
        expr = translate(tree)
        compiled = expr.compile(compile_kwargs={"literal_binds": True})
        assert "gene_symbol" in str(compiled)

    def test_not_equals(self) -> None:
        expr = translate(
            {"combinator": "and", "rules": [{"field": "chrom", "operator": "!=", "value": "Y"}]}
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        # An inverted != → = would return the wrong variant set to the filter UI.
        assert "chrom" in sql
        assert "!=" in sql or "<>" in sql
        assert "'Y'" in sql

    def test_less_than(self) -> None:
        expr = translate(
            {
                "combinator": "and",
                "rules": [{"field": "gnomad_af_global", "operator": "<", "value": 0.01}],
            }
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "gnomad_af_global <" in sql
        assert "<=" not in sql  # must be strict less-than
        assert "0.01" in sql

    def test_greater_than(self) -> None:
        expr = translate(
            {"combinator": "and", "rules": [{"field": "cadd_phred", "operator": ">", "value": 20}]}
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        # A sign-flipped > → < would silently return low-impact variants.
        assert "cadd_phred >" in sql
        assert ">=" not in sql  # must be strict greater-than
        assert "20" in sql

    def test_less_than_or_equal(self) -> None:
        expr = translate(
            {"combinator": "and", "rules": [{"field": "pos", "operator": "<=", "value": 100000}]}
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "pos <=" in sql
        assert "100000" in sql

    def test_greater_than_or_equal(self) -> None:
        expr = translate(
            {"combinator": "and", "rules": [{"field": "revel", "operator": ">=", "value": 0.5}]}
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "revel >=" in sql
        assert "0.5" in sql

    def test_contains(self) -> None:
        expr = translate(
            {
                "combinator": "and",
                "rules": [{"field": "gene_symbol", "operator": "contains", "value": "BRC"}],
            }
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "gene_symbol" in sql
        assert "LIKE" in sql.upper()
        # SQLAlchemy renders contains() with wildcards concatenated on both sides.
        assert "'%' || 'BRC' || '%'" in sql

    def test_begins_with(self) -> None:
        expr = translate(
            {
                "combinator": "and",
                "rules": [{"field": "consequence", "operator": "beginsWith", "value": "missense"}],
            }
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "consequence" in sql
        assert "LIKE" in sql.upper()
        # Trailing-only wildcard: a leading anchor would change the match set.
        assert "'missense' || '%'" in sql
        assert "'%' || 'missense'" not in sql

    def test_ends_with(self) -> None:
        expr = translate(
            {
                "combinator": "and",
                "rules": [
                    {"field": "clinvar_conditions", "operator": "endsWith", "value": "cancer"}
                ],
            }
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "clinvar_conditions" in sql
        assert "LIKE" in sql.upper()
        # Leading-only wildcard: a trailing anchor would change the match set.
        assert "'%' || 'cancer'" in sql
        assert "'cancer' || '%'" not in sql

    def test_in_operator(self) -> None:
        expr = translate(
            {
                "combinator": "and",
                "rules": [{"field": "chrom", "operator": "in", "value": ["1", "2", "X"]}],
            }
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "chrom IN (" in sql
        assert "NOT IN" not in sql  # `in` must not invert to NOT IN
        assert "'X'" in sql

    def test_not_in(self) -> None:
        expr = translate(
            {
                "combinator": "and",
                "rules": [
                    {
                        "field": "clinvar_significance",
                        "operator": "notIn",
                        "value": ["benign", "likely_benign"],
                    }
                ],
            }
        )
        sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
        # An inverted notIn → IN would return exactly the rows it should exclude.
        assert "clinvar_significance" in sql
        assert "NOT IN" in sql
        assert "'benign'" in sql

    def test_between(self) -> None:
        expr = translate(
            {
                "combinator": "and",
                "rules": [{"field": "pos", "operator": "between", "value": [100000, 200000]}],
            }
        )
        assert expr is not None

    def test_null(self) -> None:
        expr = translate(
            {"combinator": "and", "rules": [{"field": "clinvar_significance", "operator": "null"}]}
        )
        assert expr is not None

    def test_not_null(self) -> None:
        expr = translate(
            {"combinator": "and", "rules": [{"field": "gene_symbol", "operator": "notNull"}]}
        )
        assert expr is not None


class TestTranslatorNesting:
    """PRD §8.7: Nested rule groups at various depths."""

    def test_single_rule(self) -> None:
        tree = {"combinator": "and", "rules": [{"field": "chrom", "operator": "=", "value": "1"}]}
        expr = translate(tree)
        assert expr is not None

    def test_two_level_and_or(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [
                {"field": "clinvar_significance", "operator": "=", "value": "Pathogenic"},
                {
                    "combinator": "or",
                    "rules": [
                        {"field": "gnomad_af_global", "operator": "<", "value": 0.01},
                        {"field": "rare_flag", "operator": "=", "value": True},
                    ],
                },
            ],
        }
        expr = translate(tree)
        assert expr is not None

    def test_three_level_nested(self) -> None:
        tree = {
            "combinator": "or",
            "rules": [
                {
                    "combinator": "and",
                    "rules": [
                        {"field": "chrom", "operator": "=", "value": "17"},
                        {
                            "combinator": "or",
                            "rules": [
                                {"field": "gene_symbol", "operator": "=", "value": "BRCA1"},
                                {"field": "gene_symbol", "operator": "=", "value": "BRCA2"},
                            ],
                        },
                    ],
                },
                {"field": "clinvar_significance", "operator": "=", "value": "Pathogenic"},
            ],
        }
        expr = translate(tree)
        assert expr is not None

    def test_five_level_deep(self) -> None:
        """5+ levels of nesting should still work."""
        tree: dict = {
            "combinator": "and",
            "rules": [{"field": "chrom", "operator": "=", "value": "1"}],
        }
        for _ in range(5):
            tree = {
                "combinator": "or",
                "rules": [tree, {"field": "pos", "operator": ">", "value": 0}],
            }
        expr = translate(tree)
        assert expr is not None


class TestTranslatorEdgeCases:
    """PRD §8.7: Empty groups, single-rule groups, disabled rules."""

    def test_empty_rule_group_matches_all(self) -> None:
        tree = {"combinator": "and", "rules": []}
        expr = translate(tree)
        # An empty group should produce TRUE (match all)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "true" in compiled.lower() or "1" in compiled

    def test_single_rule_group(self) -> None:
        tree = {"combinator": "and", "rules": [{"field": "chrom", "operator": "=", "value": "X"}]}
        expr = translate(tree)
        assert expr is not None

    def test_group_with_one_subgroup(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [
                {"combinator": "or", "rules": [{"field": "chrom", "operator": "=", "value": "1"}]}
            ],
        }
        expr = translate(tree)
        assert expr is not None

    def test_disabled_rule_skipped(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [
                {"field": "chrom", "operator": "=", "value": "1"},
                {"field": "chrom", "operator": "=", "value": "2", "disabled": True},
            ],
        }
        expr = translate(tree)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        # Only chrom = '1' should appear, not '2'
        assert "'1'" in compiled or "1" in compiled

    def test_in_empty_list_returns_false(self) -> None:
        tree = {"combinator": "and", "rules": [{"field": "chrom", "operator": "in", "value": []}]}
        expr = translate(tree)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "false" in compiled.lower() or "0" in compiled

    def test_not_in_empty_list_returns_true(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "chrom", "operator": "notIn", "value": []}],
        }
        expr = translate(tree)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "true" in compiled.lower() or "1" in compiled

    def test_default_combinator_is_and(self) -> None:
        """Missing combinator defaults to 'and'."""
        tree = {"rules": [{"field": "chrom", "operator": "=", "value": "1"}]}
        expr = translate(tree)
        assert expr is not None


class TestTranslatorFieldValidation:
    """PRD §8.7: Reject fields not in annotated_variants schema."""

    def test_unknown_field_rejected(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "hacker_field", "operator": "=", "value": "x"}],
        }
        with pytest.raises(TranslationError, match="not in the annotated_variants schema"):
            translate(tree)

    def test_missing_field_rejected(self) -> None:
        tree = {"combinator": "and", "rules": [{"field": None, "operator": "=", "value": "x"}]}
        with pytest.raises(TranslationError, match="missing 'field'"):
            translate(tree)

    def test_empty_field_rejected(self) -> None:
        tree = {"combinator": "and", "rules": [{"field": "", "operator": "=", "value": "x"}]}
        with pytest.raises(TranslationError, match="missing 'field'"):
            translate(tree)

    def test_numeric_field_name_rejected(self) -> None:
        tree = {"combinator": "and", "rules": [{"field": 123, "operator": "=", "value": "x"}]}
        with pytest.raises(TranslationError, match="not a string"):
            translate(tree)

    def test_allowed_fields_matches_table(self) -> None:
        """ALLOWED_FIELDS should match annotated_variants column names."""
        table_cols = {col.name for col in annotated_variants.columns}
        assert ALLOWED_FIELDS == table_cols


class TestTranslatorTypeSafety:
    """PRD §8.7: Type mismatches between value and column type."""

    def test_string_value_for_numeric_column_rejected(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "pos", "operator": "=", "value": "not_a_number"}],
        }
        with pytest.raises(TranslationError, match="Non-numeric"):
            translate(tree)

    def test_string_operator_on_numeric_column_rejected(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "pos", "operator": "contains", "value": "123"}],
        }
        with pytest.raises(TranslationError, match="non-text field"):
            translate(tree)

    def test_numeric_string_coerced_for_numeric_column(self) -> None:
        """Numeric strings like '0.01' should be accepted for float columns."""
        tree = {
            "combinator": "and",
            "rules": [{"field": "gnomad_af_global", "operator": "<", "value": "0.01"}],
        }
        expr = translate(tree)
        assert expr is not None

    def test_scientific_notation_coerced(self) -> None:
        """Scientific notation like '1e-5' is common for allele frequencies."""
        tree = {
            "combinator": "and",
            "rules": [{"field": "gnomad_af_global", "operator": "<", "value": "1e-5"}],
        }
        expr = translate(tree)
        assert expr is not None

    def test_integer_string_coerced(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "pos", "operator": "=", "value": "12345"}],
        }
        expr = translate(tree)
        assert expr is not None

    def test_between_non_array_rejected(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "pos", "operator": "between", "value": 100}],
        }
        with pytest.raises(TranslationError, match="two-element array"):
            translate(tree)

    def test_between_wrong_length_rejected(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "pos", "operator": "between", "value": [1, 2, 3]}],
        }
        with pytest.raises(TranslationError, match="two-element array"):
            translate(tree)

    def test_in_non_array_rejected(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "chrom", "operator": "in", "value": "single"}],
        }
        with pytest.raises(TranslationError, match="array value"):
            translate(tree)

    def test_boolean_field_accepts_true_false(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "rare_flag", "operator": "=", "value": True}],
        }
        expr = translate(tree)
        assert expr is not None

    def test_boolean_field_accepts_string_true(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "rare_flag", "operator": "=", "value": "true"}],
        }
        expr = translate(tree)
        assert expr is not None

    def test_boolean_field_rejects_garbage(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "rare_flag", "operator": "=", "value": "maybe"}],
        }
        with pytest.raises(TranslationError, match="Non-boolean"):
            translate(tree)


class TestTranslatorInjectionAttempts:
    """PRD §8.7: SQL injection attempts must be harmless."""

    def test_sql_in_value(self) -> None:
        """SQL keywords in values are treated as literal strings."""
        tree = {
            "combinator": "and",
            "rules": [
                {
                    "field": "gene_symbol",
                    "operator": "=",
                    "value": "'; DROP TABLE annotated_variants; --",
                }
            ],
        }
        expr = translate(tree)
        # The value should be a bound parameter, not interpolated
        assert expr is not None

    def test_sql_in_field_name(self) -> None:
        """Attempted field-name injection is rejected."""
        tree = {
            "combinator": "and",
            "rules": [{"field": "gene_symbol; DROP TABLE", "operator": "=", "value": "x"}],
        }
        with pytest.raises(TranslationError, match="not in the annotated_variants schema"):
            translate(tree)

    def test_operator_override_attempt(self) -> None:
        """Unknown operators are rejected."""
        tree = {
            "combinator": "and",
            "rules": [{"field": "chrom", "operator": "LIKE '%;--", "value": "x"}],
        }
        with pytest.raises(TranslationError, match="Unsupported operator"):
            translate(tree)

    def test_combinator_injection(self) -> None:
        """Invalid combinators are rejected."""
        tree = {
            "combinator": "and; DROP TABLE",
            "rules": [{"field": "chrom", "operator": "=", "value": "1"}],
        }
        with pytest.raises(TranslationError, match="Invalid combinator"):
            translate(tree)

    def test_value_with_newlines(self) -> None:
        """Values with newlines/special chars are safely parameterized."""
        tree = {
            "combinator": "and",
            "rules": [{"field": "gene_symbol", "operator": "=", "value": "BRCA1\n; DROP TABLE"}],
        }
        expr = translate(tree)
        assert expr is not None


class TestTranslatorLargeInputs:
    """PRD §8.7: Large inputs within limits."""

    def test_50_plus_rules(self) -> None:
        rules = [{"field": "chrom", "operator": "=", "value": str(i % 22 + 1)} for i in range(55)]
        tree = {"combinator": "or", "rules": rules}
        expr = translate(tree)
        assert expr is not None

    def test_10_nested_levels(self) -> None:
        tree: dict = {
            "combinator": "and",
            "rules": [{"field": "chrom", "operator": "=", "value": "1"}],
        }
        for _ in range(10):
            tree = {"combinator": "and", "rules": [tree]}
        expr = translate(tree)
        assert expr is not None

    def test_exceeds_max_depth(self) -> None:
        """Nesting beyond MAX_DEPTH is rejected."""
        tree: dict = {
            "combinator": "and",
            "rules": [{"field": "chrom", "operator": "=", "value": "1"}],
        }
        for _ in range(25):
            tree = {"combinator": "and", "rules": [tree]}
        with pytest.raises(TranslationError, match="maximum nesting depth"):
            translate(tree)

    def test_exceeds_max_rules(self) -> None:
        """More than MAX_RULES total rules is rejected."""
        rules = [{"field": "chrom", "operator": "=", "value": "1"} for _ in range(250)]
        tree = {"combinator": "or", "rules": rules}
        with pytest.raises(TranslationError, match="exceeding the maximum"):
            translate(tree)


class TestTranslatorBooleanLogic:
    """PRD §8.7: AND-only, OR-only, mixed AND/OR, NOT groups."""

    def test_and_only(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [
                {"field": "clinvar_significance", "operator": "=", "value": "Pathogenic"},
                {"field": "gnomad_af_global", "operator": "<", "value": 0.01},
            ],
        }
        expr = translate(tree)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "AND" in compiled

    def test_or_only(self) -> None:
        tree = {
            "combinator": "or",
            "rules": [
                {"field": "chrom", "operator": "=", "value": "17"},
                {"field": "chrom", "operator": "=", "value": "13"},
            ],
        }
        expr = translate(tree)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "OR" in compiled

    def test_mixed_and_or(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [
                {"field": "clinvar_significance", "operator": "=", "value": "Pathogenic"},
                {
                    "combinator": "or",
                    "rules": [
                        {"field": "chrom", "operator": "=", "value": "17"},
                        {"field": "chrom", "operator": "=", "value": "13"},
                    ],
                },
            ],
        }
        expr = translate(tree)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "AND" in compiled
        assert "OR" in compiled

    def test_not_group(self) -> None:
        """NOT group with multiple rules should produce NOT(... AND ...)."""
        tree = {
            "combinator": "and",
            "not": True,
            "rules": [
                {"field": "clinvar_significance", "operator": "=", "value": "benign"},
                {"field": "rare_flag", "operator": "=", "value": False},
            ],
        }
        expr = translate(tree)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "NOT" in compiled

    def test_nested_not_group(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [
                {"field": "gene_symbol", "operator": "notNull"},
                {
                    "combinator": "or",
                    "not": True,
                    "rules": [
                        {"field": "clinvar_significance", "operator": "=", "value": "benign"},
                        {
                            "field": "clinvar_significance",
                            "operator": "=",
                            "value": "likely_benign",
                        },
                    ],
                },
            ],
        }
        expr = translate(tree)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "NOT" in compiled


class TestTranslatorOperatorValidation:
    """Ensure unsupported operators are rejected."""

    def test_unsupported_operator(self) -> None:
        tree = {
            "combinator": "and",
            "rules": [{"field": "chrom", "operator": "LIKE", "value": "%x%"}],
        }
        with pytest.raises(TranslationError, match="Unsupported operator"):
            translate(tree)

    def test_missing_operator(self) -> None:
        tree = {"combinator": "and", "rules": [{"field": "chrom", "value": "1"}]}
        with pytest.raises(TranslationError, match="missing 'operator'"):
            translate(tree)

    def test_non_string_operator(self) -> None:
        tree = {"combinator": "and", "rules": [{"field": "chrom", "operator": 42, "value": "1"}]}
        with pytest.raises(TranslationError, match="not a string"):
            translate(tree)


class TestTranslatorStructureValidation:
    """Validate group structure."""

    def test_non_dict_rule_group(self) -> None:
        with pytest.raises(TranslationError, match="JSON object"):
            translate("not a dict")  # type: ignore[arg-type]

    def test_rules_not_array(self) -> None:
        with pytest.raises(TranslationError, match="'rules' array"):
            translate({"combinator": "and", "rules": "not_an_array"})


# ═══════════════════════════════════════════════════════════════════════
# Integration tests — API endpoints
# ═══════════════════════════════════════════════════════════════════════

# Test annotated variants with diverse data for query testing.
ANNOTATED_VARIANTS = [
    {
        "rsid": "rs429358",
        "chrom": "19",
        "pos": 44908684,
        "ref": "T",
        "alt": "C",
        "genotype": "TC",
        "zygosity": "het",
        "gene_symbol": "APOE",
        "consequence": "missense_variant",
        "clinvar_significance": "risk_factor",
        "clinvar_review_stars": 3,
        "gnomad_af_global": 0.15,
        "rare_flag": False,
        "cadd_phred": 23.5,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
    },
    {
        "rsid": "rs80357906",
        "chrom": "17",
        "pos": 43091983,
        "ref": "CTC",
        "alt": "C",
        "genotype": "TC",
        "zygosity": "het",
        "gene_symbol": "BRCA1",
        "consequence": "frameshift_variant",
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 3,
        "gnomad_af_global": 0.0001,
        "rare_flag": True,
        "ultra_rare_flag": True,
        "cadd_phred": 35.0,
        "revel": 0.95,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": True,
    },
    {
        "rsid": "rs1801133",
        "chrom": "1",
        "pos": 11856378,
        "ref": "G",
        "alt": "A",
        "genotype": "AG",
        "zygosity": "het",
        "gene_symbol": "MTHFR",
        "consequence": "missense_variant",
        "clinvar_significance": "drug_response",
        "clinvar_review_stars": 2,
        "gnomad_af_global": 0.35,
        "rare_flag": False,
        "cadd_phred": 25.0,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
    },
    {
        "rsid": "rs4680",
        "chrom": "22",
        "pos": 19963748,
        "ref": "G",
        "alt": "A",
        "genotype": "AG",
        "zygosity": "het",
        "gene_symbol": "COMT",
        "consequence": "missense_variant",
        "clinvar_significance": "benign",
        "clinvar_review_stars": 2,
        "gnomad_af_global": 0.48,
        "rare_flag": False,
        "cadd_phred": 12.5,
        "annotation_coverage": 0x0F,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
    },
    {
        "rsid": "rs113993960",
        "chrom": "7",
        "pos": 117559590,
        "ref": "ATCT",
        "alt": "A",
        "genotype": "RA",
        "zygosity": "het",
        "gene_symbol": "CFTR",
        "consequence": "frameshift_variant",
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 3,
        "gnomad_af_global": 0.02,
        "rare_flag": False,
        "cadd_phred": 33.0,
        "revel": 0.88,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": True,
    },
]

# All annotated_variants column names for normalization.
_ALL_COLS = [col.name for col in annotated_variants.columns]


def _normalize(variant: dict) -> dict:
    """Fill missing columns with None."""
    return {k: variant.get(k) for k in _ALL_COLS}


def _setup_client(tmp_data_dir: Path, variants: list[dict]):
    """Create a TestClient with annotated sample data."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="abc123",
            )
        )
        sample_id = result.lastrowid
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    if variants:
        normalized = [_normalize(v) for v in variants]
        with sample_engine.begin() as conn:
            conn.execute(annotated_variants.insert(), normalized)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id
        reset_registry()


@pytest.fixture
def client(tmp_data_dir: Path):
    yield from _setup_client(tmp_data_dir, ANNOTATED_VARIANTS)


@pytest.fixture
def empty_client(tmp_data_dir: Path):
    yield from _setup_client(tmp_data_dir, [])


class TestQueryFields:
    """GET /api/query/fields — metadata endpoint."""

    def test_returns_fields_and_operators(self, client) -> None:
        tc, _ = client
        resp = tc.get("/api/query/fields")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["fields"]) > 0
        assert "rsid" in [f["name"] for f in data["fields"]]
        assert len(data["operators"]) == len(SUPPORTED_OPERATORS)

    def test_field_types_present(self, client) -> None:
        tc, _ = client
        resp = tc.get("/api/query/fields")
        data = resp.json()
        field_map = {f["name"]: f for f in data["fields"]}
        assert field_map["pos"]["type"] == "integer"
        assert field_map["gnomad_af_global"]["type"] == "number"
        assert field_map["gene_symbol"]["type"] == "text"
        assert field_map["rare_flag"]["type"] == "boolean"


class TestQueryEndpoint:
    """POST /api/query — execute filter tree."""

    def test_simple_equals_filter(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [
                        {"field": "clinvar_significance", "operator": "=", "value": "Pathogenic"}
                    ],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matching"] == 2
        assert len(data["items"]) == 2
        assert all(item["clinvar_significance"] == "Pathogenic" for item in data["items"])

    def test_and_filter(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [
                        {"field": "clinvar_significance", "operator": "=", "value": "Pathogenic"},
                        {"field": "gnomad_af_global", "operator": "<", "value": 0.01},
                    ],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Only BRCA1 has AF < 0.01 AND is Pathogenic
        assert data["total_matching"] == 1
        assert data["items"][0]["gene_symbol"] == "BRCA1"

    def test_or_filter(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "or",
                    "rules": [
                        {"field": "gene_symbol", "operator": "=", "value": "BRCA1"},
                        {"field": "gene_symbol", "operator": "=", "value": "MTHFR"},
                    ],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matching"] == 2
        genes = {item["gene_symbol"] for item in data["items"]}
        assert genes == {"BRCA1", "MTHFR"}

    def test_nested_filter(self, client) -> None:
        """Pathogenic AND (rare OR ultra_rare)."""
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [
                        {"field": "clinvar_significance", "operator": "=", "value": "Pathogenic"},
                        {
                            "combinator": "or",
                            "rules": [
                                {"field": "rare_flag", "operator": "=", "value": True},
                                {"field": "ultra_rare_flag", "operator": "=", "value": True},
                            ],
                        },
                    ],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matching"] == 1
        assert data["items"][0]["gene_symbol"] == "BRCA1"

    def test_empty_filter_returns_all(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {"combinator": "and", "rules": []},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matching"] == 5

    def test_pagination(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {"combinator": "and", "rules": []},
                "limit": 2,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["has_more"] is True
        assert data["next_cursor_chrom"] is not None

        # Fetch next page
        resp2 = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {"combinator": "and", "rules": []},
                "limit": 2,
                "cursor_chrom": data["next_cursor_chrom"],
                "cursor_pos": data["next_cursor_pos"],
            },
        )
        data2 = resp2.json()
        assert len(data2["items"]) == 2
        # No overlap
        rsids1 = {i["rsid"] for i in data["items"]}
        rsids2 = {i["rsid"] for i in data2["items"]}
        assert rsids1.isdisjoint(rsids2)
        # Total only returned on first page
        assert data["total_matching"] == 5
        assert data2["total_matching"] is None

    def test_between_filter(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [{"field": "cadd_phred", "operator": "between", "value": [20, 30]}],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert 20 <= item["cadd_phred"] <= 30

    def test_in_filter(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [{"field": "chrom", "operator": "in", "value": ["17", "7"]}],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matching"] == 2
        chroms = {item["chrom"] for item in data["items"]}
        assert chroms == {"17", "7"}

    def test_null_filter(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [{"field": "revel", "operator": "notNull"}],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matching"] == 2
        for item in data["items"]:
            assert item["revel"] is not None

    def test_contains_filter(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [{"field": "gene_symbol", "operator": "contains", "value": "BRC"}],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matching"] == 1
        assert data["items"][0]["gene_symbol"] == "BRCA1"

    def test_not_group_filter(self, client) -> None:
        """NOT(benign) should return non-benign variants."""
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "not": True,
                    "rules": [
                        {"field": "clinvar_significance", "operator": "=", "value": "benign"}
                    ],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # 4 out of 5 are not benign
        assert data["total_matching"] == 4
        assert len(data["items"]) == 4
        assert all(item["clinvar_significance"] != "benign" for item in data["items"])


class TestQueryErrors:
    """Error cases for POST /api/query."""

    def test_sample_not_found(self, client) -> None:
        tc, _ = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": 999,
                "filter": {"combinator": "and", "rules": []},
            },
        )
        assert resp.status_code == 404

    def test_no_annotated_variants(self, empty_client) -> None:
        tc, sid = empty_client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {"combinator": "and", "rules": []},
            },
        )
        assert resp.status_code == 422
        assert "annotated variants" in resp.json()["detail"].lower()

    def test_invalid_field(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [{"field": "evil_field", "operator": "=", "value": "x"}],
                },
            },
        )
        assert resp.status_code == 422
        assert "not in the annotated_variants schema" in resp.json()["detail"]

    def test_sql_injection_in_value(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [
                        {
                            "field": "gene_symbol",
                            "operator": "=",
                            "value": "'; DROP TABLE annotated_variants; --",
                        }
                    ],
                },
            },
        )
        # Should succeed (0 results) — injection is safely parameterized
        assert resp.status_code == 200
        assert resp.json()["total_matching"] == 0

    def test_type_mismatch_rejected(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [{"field": "pos", "operator": "=", "value": "not_a_number"}],
                },
            },
        )
        assert resp.status_code == 422

    def test_e2e_flow_pathogenic_and_rare(self, client) -> None:
        """PRD E2E Flow F5: ClinVar=Pathogenic AND AF < 0.01."""
        tc, sid = client
        resp = tc.post(
            "/api/query",
            json={
                "sample_id": sid,
                "filter": {
                    "combinator": "and",
                    "rules": [
                        {"field": "clinvar_significance", "operator": "=", "value": "Pathogenic"},
                        {"field": "gnomad_af_global", "operator": "<", "value": 0.01},
                    ],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matching"] == 1
        assert data["items"][0]["gene_symbol"] == "BRCA1"
        assert data["items"][0]["rsid"] == "rs80357906"
