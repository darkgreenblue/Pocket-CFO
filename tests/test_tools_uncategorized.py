from bot.llm.tools import _filter_by_category, dispatch
from bot.services import transactions as ts


def test_uncategorized_lists_untagged(db):
    ts.create_from_item(1, {"title": "شام", "amount": 150000, "suggested_tags": ["رستوران"]})
    ts.create_from_item(1, {"title": "یه چیز مبهم", "amount": 90000, "suggested_tags": []})
    ts.create_from_item(1, {"title": "خرجِ نامشخص", "amount": 40000})

    res = dispatch("list_transactions", {"period": "all", "category": "بدون دسته"}, user_id=1)
    titles = sorted(t["title"] for t in res["transactions"])
    assert res["count"] == 2
    assert titles == ["خرجِ نامشخص", "یه چیز مبهم"]


def test_uncategorized_aliases(db):
    ts.create_from_item(1, {"title": "بی‌تگ", "amount": 1000, "suggested_tags": []})
    ts.create_from_item(1, {"title": "کافه", "amount": 2000, "suggested_tags": ["کافه"]})
    for alias in ("بدون‌دسته", "دسته‌بندی نشده", "بدون تگ"):
        res = dispatch("list_transactions", {"period": "all", "category": alias}, user_id=1)
        assert res["count"] == 1, alias
        assert res["transactions"][0]["title"] == "بی‌تگ"


def test_summary_and_detail_agree_on_uncategorized(db):
    ts.create_from_item(1, {"title": "نامشخص", "amount": 5000, "suggested_tags": []})
    summ = dispatch("get_summary", {"period": "all"}, user_id=1)
    assert "بدون دسته" in summ["toman_by_category"]
    detail = dispatch("list_transactions", {"period": "all", "category": "بدون دسته"}, user_id=1)
    assert detail["count"] == 1
