"""Tests for the Strategy Catalog's real tunable-parameter overrides
(domain/leaderboard/strategy_overrides.py) -- the Edit page's data layer."""

from __future__ import annotations

import pytest

from dashboard.backend.domain.leaderboard import strategy_overrides as so


def test_schema_for_known_strategy_matches_its_param_schema():
    schema = so.schema_for("momentum_effect")
    assert set(schema) == {"top_n", "rebalance_days", "lookback_days"}
    assert schema["top_n"]["default"] == 10


def test_schema_for_strategy_with_no_tunables_is_empty():
    assert so.schema_for("mean_variance_djia") == {}
    assert so.schema_for("equal_weight_djia") == {}


def test_schema_for_unknown_key_is_empty():
    assert so.schema_for("not_a_real_strategy") == {}


def test_effective_params_falls_back_to_schema_defaults():
    values = so.effective_params("momentum_scout")
    schema = so.schema_for("momentum_scout")
    assert values == {name: spec["default"] for name, spec in schema.items()}


def test_set_overrides_rejects_unknown_param():
    schema = so.schema_for("momentum_effect")
    with pytest.raises(ValueError, match="not a tunable parameter"):
        so.set_overrides("momentum_effect", schema, {"not_a_real_param": 1})


def test_set_overrides_rejects_out_of_range():
    schema = so.schema_for("momentum_effect")
    with pytest.raises(ValueError, match="must be <="):
        so.set_overrides("momentum_effect", schema, {"top_n": 999})


def test_set_overrides_rejects_non_numeric():
    schema = so.schema_for("momentum_effect")
    with pytest.raises(ValueError, match="must be a number"):
        so.set_overrides("momentum_effect", schema, {"top_n": "not-a-number"})


def test_set_overrides_requires_a_schema():
    with pytest.raises(ValueError, match="no tunable parameters"):
        so.set_overrides("mean_variance_djia", {}, {"anything": 1})


def test_set_overrides_persists_and_effective_params_reflects_it():
    schema = so.schema_for("momentum_effect")
    so.set_overrides("momentum_effect", schema, {"top_n": 4})
    try:
        assert so.effective_params("momentum_effect")["top_n"] == 4
        assert so.get_overrides("momentum_effect") == {"top_n": 4}
    finally:
        so.set_overrides("momentum_effect", schema, {"top_n": schema["top_n"]["default"]})


def test_apply_overrides_merges_into_config_and_is_a_noop_with_none_saved():
    base = {"strategy": "momentum_scout", "symbols": ["AAPL"]}
    assert so.apply_overrides("momentum_scout", base) == base  # no overrides saved yet

    schema = so.schema_for("momentum_scout")
    so.set_overrides("momentum_scout", schema, {"top_n": 3})
    try:
        merged = so.apply_overrides("momentum_scout", base)
        assert merged["top_n"] == 3
        assert merged["strategy"] == "momentum_scout"
        assert base == {"strategy": "momentum_scout", "symbols": ["AAPL"]}  # base untouched
    finally:
        so.set_overrides("momentum_scout", schema, {"top_n": schema["top_n"]["default"]})
