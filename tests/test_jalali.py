from bot.utils import jalali


def test_month_number_from_text():
    assert jalali.month_number("این خرج مال خردادماهه") == 3
    assert jalali.month_number("آبان") == 8
    assert jalali.month_number("چیزی نیست") is None


def test_month_last_day():
    assert jalali.month_last_day(1403, 1) == 31
    assert jalali.month_last_day(1403, 8) == 30
    assert jalali.month_last_day(1403, 12) == 30  # کبیسه
    assert jalali.month_last_day(1404, 12) == 29


def test_resolve_past_month_year_inference():
    cy, cm = jalali.current_ym()
    # ماهی که ≤ ماه جاری است → همین سال
    y, m = jalali.resolve_past_month(jalali.month_name(cm))
    assert (y, m) == (cy, cm)
    # ماه بعد از ماه جاری → سال قبل (اشاره به گذشته)
    nxt = cm % 12 + 1
    y2, m2 = jalali.resolve_past_month(jalali.month_name(nxt))
    assert m2 == nxt and y2 == cy - 1
