from bot.services.tags import normalize, reconcile

TAG_BANK = [
    {"id": 1, "name": "خوراکی", "aliases": ["غذا", "وعده غذایی", "خوراک"]},
    {"id": 2, "name": "سوپرمارکت", "aliases": ["سوپر", "بقالی"]},
    {"id": 3, "name": "حمل‌ونقل", "aliases": ["تاکسی", "اسنپ"]},
]


def test_normalize_arabic_chars_and_zwnj():
    assert normalize("حمل‌ونقل") == "حمل ونقل"
    assert normalize("كافي") == "کافی"


def test_exact_match():
    ids, names, unmatched = reconcile(["خوراکی"], TAG_BANK)
    assert ids == [1]
    assert unmatched == []


def test_alias_match_prevents_duplicate_concepts():
    # «وعده غذایی» باید به همان تگ کانونی «خوراکی» نگاشت شود
    ids, names, unmatched = reconcile(["وعده غذایی"], TAG_BANK)
    assert ids == [1]
    assert names == ["خوراکی"]
    assert unmatched == []


def test_alias_match_taxi_to_transport():
    ids, _, _ = reconcile(["اسنپ"], TAG_BANK)
    assert ids == [3]


def test_unmatched_goes_to_suggestions():
    ids, names, unmatched = reconcile(["سرمایه‌گذاری در بورس"], TAG_BANK)
    assert ids == []
    assert unmatched == ["سرمایه‌گذاری در بورس"]


def test_no_duplicate_ids():
    ids, _, _ = reconcile(["خوراکی", "غذا", "خوراک"], TAG_BANK)
    assert ids == [1]
