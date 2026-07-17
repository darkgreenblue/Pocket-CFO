from bot.llm.router import Decision, parse_decision


def test_parse_plain_json():
    d = parse_decision('{"record": true, "data_query": false, "reply": ""}')
    assert d == Decision(record=True, data_query=False, reply="")


def test_parse_data_query_with_reply():
    d = parse_decision('{"record": false, "data_query": true, "reply": "الان نگاه می‌کنم"}')
    assert d.data_query is True and d.record is False
    assert d.reply == "الان نگاه می‌کنم"


def test_parse_fenced_and_extra_text():
    raw = "```json\n{\"record\": false, \"data_query\": false, \"reply\": \"سلام!\"}\n```"
    d = parse_decision(raw)
    assert d.reply == "سلام!" and not d.record and not d.data_query


def test_parse_embedded_json():
    raw = 'یک لحظه: {"record": true, "data_query": true, "reply": ""} تمام.'
    d = parse_decision(raw)
    assert d.record and d.data_query


def test_parse_garbage_falls_back_to_conversational():
    # خروجیِ نامعتبر نباید به «باشه»ِ خالی برسد؛ محافظه‌کارانه گفتگویی فرض می‌شود.
    d = parse_decision("سلام، چطوری؟")
    assert d.record is False and d.data_query is False
    assert d.reply == "سلام، چطوری؟"


def test_missing_fields_default_false():
    d = parse_decision('{"reply": "باشه حتماً"}')
    assert d.record is False and d.data_query is False and d.reply == "باشه حتماً"
