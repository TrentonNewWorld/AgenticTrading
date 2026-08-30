"""State-machine tests for domain/prediction/repository.py's unified queue --
the same table serves Manual, My Agents, and Testing/Upload (see that
module's docstring for why there's one table instead of three).
"""

from __future__ import annotations

import pytest

from dashboard.backend.domain.prediction import repository as repo

repo.init_schema()


def test_create_lands_in_waiting_with_zero_days():
    row = repo.create(name="Test Strategy", description="desc", source_type="manual", code="CODE")
    assert row["status"] == "waiting"
    assert row["day_count"] == 0
    assert row["cash"] == repo.DEFAULT_INITIAL_CAPITAL
    assert row["equity_curve"] == []


def test_create_rejected_never_enters_the_waiting_list():
    row = repo.create_rejected(
        name="Bad Strategy", description="", source_type="upload", code="bad code", error="syntax error",
    )
    assert row["status"] == "rejected"
    assert row["day_count"] == 0
    # A rejected row must never show up in the scheduler's worklist.
    assert row["id"] not in {r["id"] for r in repo.list_due_for_tick("2026-01-01")}


def test_get_with_code_hides_code_from_get():
    row = repo.create(name="Secret", description="", source_type="manual", code="SECRET_CODE")
    assert "code" not in repo.get(row["id"])
    assert repo.get_with_code(row["id"])["code"] == "SECRET_CODE"


def test_list_due_for_tick_includes_waiting_not_yet_ticked_today():
    row = repo.create(name="Due Today", description="", source_type="manual", code="C")
    due = repo.list_due_for_tick("2026-06-01")
    assert row["id"] in {r["id"] for r in due}


def test_list_due_for_tick_excludes_already_ticked_today():
    row = repo.create(name="Already Ticked", description="", source_type="manual", code="C")
    repo.record_tick(
        row["id"], as_of="2026-06-01", cash=1000.0, positions=[],
        equity_point={"date": "2026-06-01", "equity": 1000.0}, fees_paid_today=0.0,
    )
    due = repo.list_due_for_tick("2026-06-01")
    assert row["id"] not in {r["id"] for r in due}
    # But it's due again on a new day.
    due_next_day = repo.list_due_for_tick("2026-06-02")
    assert row["id"] in {r["id"] for r in due_next_day}


def test_five_ticks_promotes_waiting_to_ready():
    row = repo.create(name="Five Day Test", description="", source_type="manual", code="C")
    dates = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    for i, d in enumerate(dates):
        row = repo.record_tick(
            row["id"], as_of=d, cash=1000.0, positions=[],
            equity_point={"date": d, "equity": 1000.0}, fees_paid_today=0.0,
        )
        if i < 4:
            assert row["status"] == "waiting", f"should still be waiting after tick {i + 1}"
        else:
            assert row["status"] == "ready", "should flip to ready on the 5th tick"
    assert row["day_count"] == 5
    assert row["ready_at"] is not None


def test_ready_strategy_is_not_ticked_again_until_added():
    row = repo.create(name="Frozen", description="", source_type="manual", code="C")
    for i, d in enumerate(["2026-07-0" + str(n) for n in range(1, 6)]):
        row = repo.record_tick(
            row["id"], as_of=d, cash=1000.0, positions=[],
            equity_point={"date": d, "equity": 1000.0}, fees_paid_today=0.0,
        )
    assert row["status"] == "ready"
    due = repo.list_due_for_tick("2026-07-06")
    assert row["id"] not in {r["id"] for r in due}, "a ready strategy awaiting a human decision must not keep ticking"


def test_mark_added_resumes_ticking_and_never_regresses_to_ready():
    row = repo.create(name="Kept Strategy", description="", source_type="manual", code="C")
    for d in ["2026-08-0" + str(n) for n in range(1, 6)]:
        row = repo.record_tick(
            row["id"], as_of=d, cash=1000.0, positions=[],
            equity_point={"date": d, "equity": 1000.0}, fees_paid_today=0.0,
        )
    assert row["status"] == "ready"
    repo.mark_added(row["id"])
    assert repo.get(row["id"])["status"] == "added"

    due = repo.list_due_for_tick("2026-08-06")
    assert row["id"] in {r["id"] for r in due}, "an added strategy keeps trading forward indefinitely"

    ticked = repo.record_tick(
        row["id"], as_of="2026-08-06", cash=1000.0, positions=[],
        equity_point={"date": "2026-08-06", "equity": 1050.0}, fees_paid_today=0.0,
    )
    assert ticked["status"] == "added", "ticking an added strategy must not regress it back to ready"
    assert ticked["day_count"] == 6


def test_record_tick_accumulates_fees_and_curve():
    row = repo.create(name="Fee Test", description="", source_type="manual", code="C")
    # A realistic position shape -- other tests' tick_all() calls in this
    # same pytest session share this DB and would KeyError on a malformed
    # one left lying around in 'waiting' status.
    position = {"platform": "kalshi", "market_id": "X", "outcome": "yes", "side": "buy", "qty": 1, "entry_price": 0.5}
    row = repo.record_tick(
        row["id"], as_of="2026-09-01", cash=990.0, positions=[position],
        equity_point={"date": "2026-09-01", "equity": 995.0}, fees_paid_today=1.75,
    )
    assert row["total_fees_paid"] == pytest.approx(1.75)
    assert row["equity_curve"] == [{"date": "2026-09-01", "equity": 995.0}]
    assert row["positions"] == [position]
    repo.mark_deleted(row["id"])  # keep it out of later tests' tick_all() worklists


def test_mark_deleted_and_permanently_delete():
    row = repo.create(name="To Delete", description="", source_type="manual", code="C")
    repo.mark_deleted(row["id"])
    assert repo.get(row["id"])["status"] == "deleted"

    rejected = repo.create_rejected(name="Bad", description="", source_type="upload", code="c", error="e")
    repo.delete_permanently(rejected["id"])
    assert repo.get(rejected["id"]) is None
