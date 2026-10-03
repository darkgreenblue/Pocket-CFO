"""ورودیِ عکس: رسیدِ چندقلمی، پیامکِ بانکی و گردشِ حسابِ موبایل‌بانک.

مدل فقط «می‌خواند»؛ هرچه حساب است (ریال→تومان، تعداد×فی، واریزها، عنوانِ عمومیِ بانک،
کنترلِ جمع) کارِ کد است و این تست‌ها نگهبانش‌اند. خروجیِ «مدلِ بی‌نقص» از روی همان
ground truthِ عکس‌های نمونه (tests/fixtures/vision/expected.json) ساخته می‌شود.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.flows.draft_flow import render_card
from bot.llm import agent, vision

USER = 1
FIXTURES = Path(__file__).parent / "fixtures" / "vision"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


# ---------- خروجیِ «مدلِ بی‌نقص» از روی ground truth ----------

def _receipt_output(key: str, unit: str = "rial") -> dict:
    gt = EXPECTED[key]
    return {
        "kind": "receipt", "merchant": "فروشگاه زنجیره‌ای نمونه", "date": None,
        "unit": unit, "unit_reason": "سرستون ریال",
        "items": [{"name": it["name"], "quantity": it.get("quantity"),
                   "unit_price": it.get("unit_price"), "line_total": it.get("line_total"),
                   "direction": "out", "raw": it["name"], "suggested_tags": []}
                  for it in gt["items"]],
        "items_total": gt.get("subtotal"), "discount_total": gt.get("discount"),
        "tax_total": gt.get("tax"), "payable_total": gt.get("payable"), "reply": "",
    }


def _bank_output(key: str, kind: str) -> dict:
    gt = EXPECTED[key]
    items = [{"name": None, "line_total": w["amount"], "direction": "out",
              "description": w.get("description"), "date": w.get("date"),
              "time": w.get("time"), "raw": f"برداشت {w['amount']}"} for w in gt["withdrawals"]]
    items += [{"name": None, "line_total": d["amount"], "direction": "in",
               "description": d.get("description"), "raw": f"واریز {d['amount']}"}
              for d in gt["deposits"]]
    return {"kind": kind, "merchant": "بانک نمونه", "unit": "rial", "items": items, "reply": ""}


@pytest.fixture
def mehr_1405(monkeypatch):
    """«امروز» را مهرِ ۱۴۰۵ می‌گذاریم تا تاریخ‌های عکس‌های نمونه «ماهِ جاری» باشند."""
    from bot.utils import jalali
    monkeypatch.setattr(jalali, "current_ym", lambda: (1405, 7))


# ---------- تبدیلِ واحد و مبلغِ ردیف ----------

def test_rial_is_divided_by_ten_and_rounded():
    assert vision.convert(1_500_000, "rial") == (150_000, "toman")
    assert vision.convert(363_750, "rial") == (36_375, "toman")
    assert vision.convert(1_234_565, "rial") == (123_457, "toman")     # نیم به بالا
    assert vision.convert(150_000, "toman") == (150_000, "toman")
    assert vision.convert(12.5, "usd") == (12.5, "usd")
    assert vision.convert(None, "rial") == (None, "toman")


def test_line_amount_prefers_printed_total_then_quantity_times_price():
    assert vision.line_amount({"line_total": 1_500_000, "quantity": 2, "unit_price": 1}) == 1_500_000
    assert vision.line_amount({"quantity": 0.75, "unit_price": 485_000}) == 363_750
    assert vision.line_amount({"quantity": None, "unit_price": 900_000}) == 900_000
    assert vision.line_amount({"line_total": "۱٬۲۵۰٬۰۰۰"}) == 1_250_000
    assert vision.line_amount({"line_total": "-640,000"}) == 640_000
    assert vision.line_amount({}) is None


def test_missing_unit_defaults_to_rial():
    reading = vision.normalize({"kind": "bank_sms", "items": [
        {"line_total": 640_000, "direction": "out", "raw": "برداشت"}]})
    assert reading.source_unit == "rial"
    assert reading.lines[0].amount == 64_000


# ---------- رسید ----------

def test_supermarket_receipt_every_item_becomes_its_own_line(mehr_1405):
    gt = EXPECTED["receipt_supermarket_15.png"]
    reading = vision.normalize(_receipt_output("receipt_supermarket_15.png"))

    assert len(reading.lines) == 15
    assert [ln.title for ln in reading.lines] == [it["name"] for it in gt["items"]]
    assert [ln.amount for ln in reading.lines] == [
        vision.convert(it["line_total"], "rial")[0] for it in gt["items"]]
    assert all(ln.currency == "toman" for ln in reading.lines)
    assert reading.check.status == "ok"
    assert reading.converted_from_rial


def test_receipt_summary_mentions_conversion_and_untaxed_payable(mehr_1405):
    reading = vision.normalize(_receipt_output("receipt_supermarket_15.png"))
    text = vision.summary(reading, created=15, incomplete=0)

    assert "۱۵ قلم ثبت شد" in text
    assert "ریالی بود" in text
    assert "مالیات/تخفیف" in text and "۲٬۲۷۰٬۸۱۳ تومان" in text   # 22,708,125 ریال
    assert "نمی‌خونه" not in text


def test_quantity_note_is_shown_in_toman(mehr_1405):
    reading = vision.normalize(_receipt_output("receipt_supermarket_15.png"))
    milk = reading.lines[0]
    assert "۲ × ۷۵٬۰۰۰ تومان" in milk.note


def test_toman_receipt_is_not_converted():
    data = _receipt_output("receipt_cafe_no_unit.png", unit="toman")
    reading = vision.normalize(data)
    first = EXPECTED["receipt_cafe_no_unit.png"]["items"][0]
    assert reading.lines[0].amount == first["line_total"]
    assert "ریالی بود" not in vision.summary(reading, created=5, incomplete=0)


def test_missed_item_triggers_a_total_mismatch_warning(mehr_1405):
    data = _receipt_output("receipt_supermarket_15.png")
    data["items"] = data["items"][:-1]                     # مدل قلمِ آخر را جا انداخت
    reading = vision.normalize(data)

    assert reading.check.status == "mismatch"
    text = vision.summary(reading, created=14, incomplete=0)
    assert "نمی‌خونه" in text and "چک کن" in text


def test_total_check_falls_back_to_payable_with_discount_and_tax():
    data = {"kind": "receipt", "unit": "rial", "items": [
        {"name": "الف", "line_total": 1_000_000}, {"name": "ب", "line_total": 2_000_000}],
        "items_total": None, "discount_total": 100_000, "tax_total": 290_000,
        "payable_total": 3_190_000}
    assert vision.normalize(data).check.status == "ok"

    # اقلامِ خالص (تخفیف/مالیات از قبل روی ردیف‌ها) هم قبول است.
    data.update(discount_total=None, tax_total=None, payable_total=3_000_000)
    assert vision.normalize(data).check.status == "ok"


def test_unreadable_row_is_kept_as_an_incomplete_line():
    data = {"kind": "receipt", "unit": "rial", "items": [
        {"name": "نان", "line_total": 300_000, "raw": "نان 300,000"},
        {"name": None, "line_total": 950_000, "raw": "?? 950,000"},
        {"name": "ماست", "line_total": None, "raw": "ماست ??"},
        {"name": None, "line_total": None, "raw": ""}],    # کاملاً خالی → حذف
        "items_total": 2_000_000}
    reading = vision.normalize(data)

    assert [(ln.title, ln.amount) for ln in reading.lines] == [
        ("نان", 30_000), (None, 95_000), ("ماست", None)]
    assert reading.check.status == "unverifiable"          # با ردیفِ بی‌مبلغ قضاوت نمی‌کنیم


def test_too_many_rows_are_capped():
    items = [{"name": f"قلم {i}", "line_total": 10_000} for i in range(vision.MAX_LINES + 5)]
    reading = vision.normalize({"kind": "receipt", "unit": "toman", "items": items})
    assert len(reading.lines) == vision.MAX_LINES
    assert reading.dropped_over_limit == 5
    assert "ردیفِ آخر ثبت نشد" in vision.summary(reading, vision.MAX_LINES, 0)


# ---------- پیامکِ بانکی و موبایل‌بانک ----------

def test_bank_sms_withdrawals_only_without_names(mehr_1405):
    gt = EXPECTED["bank_sms_list.png"]
    reading = vision.normalize(_bank_output("bank_sms_list.png", "bank_sms"))

    assert len(reading.lines) == len(gt["withdrawals"]) == 4
    assert reading.deposits_ignored == 1
    assert all(ln.title is None for ln in reading.lines)
    assert [ln.amount for ln in reading.lines] == [w["amount"] // 10 for w in gt["withdrawals"]]
    text = vision.summary(reading, created=4, incomplete=4)
    assert "۴ برداشت ثبت شد" in text
    assert "اسم ندارن" in text
    assert "۱ واریز" in text


def test_bank_sms_note_keeps_label_and_date(mehr_1405):
    data = {"kind": "bank_sms", "unit": "rial", "merchant": "بانک نمونه", "items": [
        {"name": "خرید", "line_total": 640_000, "direction": "out", "description": "خرید",
         "date": "1405/07/08", "time": "09:15"}]}
    line = vision.normalize(data).lines[0]
    assert line.title is None                    # «خرید» برچسبِ عمومی است، نه اسم
    assert "خرید" in line.note and "۱۴۰۵/۰۷/۰۸" in line.note and "بانک نمونه" in line.note
    assert line.period is None                   # همین ماه


def test_bank_app_keeps_specific_merchant_names(mehr_1405):
    data = {"kind": "bank_app", "unit": "rial", "items": [
        {"name": "دیجی‌کالا", "line_total": 8_900_000, "direction": "out",
         "description": "خرید اینترنتی - دیجی‌کالا"},
        {"name": "کارت به کارت", "line_total": 2_000_000, "direction": "out"},
        {"name": "واریز حقوق", "line_total": 450_000_000, "direction": "in"}]}
    reading = vision.normalize(data)
    assert [ln.title for ln in reading.lines] == ["دیجی‌کالا", None]
    assert reading.deposits_ignored == 1


def test_previous_month_rows_are_filed_in_that_month(mehr_1405):
    data = {"kind": "bank_sms", "unit": "rial", "items": [
        {"line_total": 100_000, "direction": "out", "date": "1405/06/30"},
        {"line_total": 100_000, "direction": "out", "date": "۱۴۰۵/۰۷/۰۱"},
        {"line_total": 100_000, "direction": "out", "date": "1403/01/01"}]}   # خیلی قدیمی → نادیده
    assert [ln.period for ln in vision.normalize(data).lines] == [(1405, 6), None, None]


def test_caption_past_month_overrides_row_dates(mehr_1405):
    data = {"kind": "receipt", "unit": "toman", "past_month": "خرداد",
            "items": [{"name": "نان", "line_total": 30_000}]}
    assert vision.normalize(data).lines[0].period == (1405, 3)


# ---------- عکس‌های غیرمالی/ناخوانا و parse ----------

def test_not_financial_and_unreadable_record_nothing():
    for kind, reply in (("not_financial", vision.NOT_FINANCIAL_REPLY),
                        ("unreadable", vision.UNREADABLE_REPLY)):
        reading = vision.normalize({"kind": kind, "items": [{"name": "x", "line_total": 1}]})
        assert reading.lines == []
        assert vision.summary(reading, 0, 0) == reply


def test_loads_tolerates_fences_and_garbage():
    assert vision.loads('```json\n{"kind": "receipt", "items": []}\n```')["kind"] == "receipt"
    assert vision.loads('حتماً! {"kind": "bank_sms"} تمام')["kind"] == "bank_sms"
    assert vision.loads("not json at all")["kind"] == "unreadable"


def test_summary_never_claims_recording_when_nothing_was_created():
    from bot.handlers.messages import claims_action
    reading = vision.normalize({"kind": "receipt", "unit": "rial", "items": []})
    text = vision.summary(reading, created=0, incomplete=0)
    assert not claims_action(text)
    assert not claims_action(vision.NOT_FINANCIAL_REPLY)
    assert not claims_action(vision.UNREADABLE_REPLY)


# ---------- agent.converse_image: از JSONِ مدل تا ردیف‌های دیتابیس ----------

class _FakeChat:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    async def __call__(self, messages, tools=None, json_mode=False, **kwargs):
        self.calls.append({"messages": messages, "json_mode": json_mode, **kwargs})
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False),
                               tool_calls=None)


def test_converse_image_creates_one_transaction_per_receipt_row(db, monkeypatch, mehr_1405):
    fake = _FakeChat(_receipt_output("receipt_supermarket_15.png"))
    monkeypatch.setattr(agent, "chat", fake)

    result = asyncio.run(agent.converse_image(image=b"\xff\xd8fake", mime="image/jpeg",
                                              caption="خرید امروز", user_id=USER,
                                              allowed_tags=["لبنیات"]))

    assert len(result.created) == 15
    txns = [db.get_transaction(t) for t in result.created]
    assert all(t["source"] == "photo" and t["status"] == "confirmed" for t in txns)
    assert txns[0]["amount"] == 150_000 and txns[0]["currency_display"] == "toman"
    assert "۱۵ قلم ثبت شد" in result.reply
    assert result.transcript.startswith("[عکس: رسید")

    call = fake.calls[0]
    assert call["json_mode"] is True
    assert call["timeout"] >= 30 and call["max_tokens"] >= 8000
    roles = [m["role"] for m in call["messages"]]
    assert roles == ["system", "user"]                     # بدونِ تاریخچه
    parts = call["messages"][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "خرید امروز" in parts[0]["text"]
    assert "لبنیات" in call["messages"][0]["content"]


def test_converse_image_bank_rows_are_drafts_needing_a_title(db, monkeypatch, mehr_1405):
    monkeypatch.setattr(agent, "chat", _FakeChat(_bank_output("bank_sms_list.png", "bank_sms")))
    result = asyncio.run(agent.converse_image(image=b"x", user_id=USER))

    txns = [db.get_transaction(t) for t in result.created]
    assert len(txns) == 4
    assert all(t["status"] == "draft" and not t["title"] for t in txns)
    assert sorted(t["amount"] for t in txns) == [64_000, 125_000, 380_000, 1_250_000]
    card, _ = render_card(txns[0])
    assert "نیازمند تکمیل: عنوان" in card


def test_photo_card_details_show_the_raw_row(db, monkeypatch):
    monkeypatch.setattr(agent, "chat", _FakeChat({"kind": "receipt", "unit": "rial", "items": [
        {"name": "شیر", "line_total": 1_500_000, "raw": "شیر کم‌چرب 2 750,000 1,500,000"}]}))
    result = asyncio.run(agent.converse_image(image=b"x", user_id=USER))
    expanded, _ = render_card(db.get_transaction(result.created[0]), expanded=True)
    assert "🖼 روی عکس: «شیر کم‌چرب 2 750,000 1,500,000»" in expanded
    assert "شنیدم" not in expanded
