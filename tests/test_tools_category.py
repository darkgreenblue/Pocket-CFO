from bot.llm.tools import _filter_by_category, dispatch
from bot.services import transactions as ts


def test_list_transactions_by_category(db):
    ts.create_from_item(1, {"title": "شام بیرون", "amount": 150000, "suggested_tags": ["رستوران"]})
    ts.create_from_item(1, {"title": "کادوی تولد", "amount": 500000, "suggested_tags": ["هدیه"]})
    ts.create_from_item(1, {"title": "کادوی سالگرد", "amount": 800000, "suggested_tags": ["هدیه"]})

    res = dispatch("list_transactions", {"period": "all", "category": "هدیه"}, user_id=1)
    titles = sorted(t["title"] for t in res["transactions"])
    assert res["count"] == 2
    assert titles == ["کادوی تولد", "کادوی سالگرد"]
    assert res.get("category") == "هدیه"
    assert "totals_by_currency" in res


def test_list_transactions_without_category_returns_all(db):
    ts.create_from_item(1, {"title": "شام", "amount": 150000, "suggested_tags": ["رستوران"]})
    ts.create_from_item(1, {"title": "کادو", "amount": 500000, "suggested_tags": ["هدیه"]})
    res = dispatch("list_transactions", {"period": "all"}, user_id=1)
    assert res["count"] == 2
    assert "category" not in res


def test_filter_by_category_empty_returns_input():
    txns = [{"title": "x", "tags": ["رستوران"]}]
    assert _filter_by_category(txns, "") == txns
