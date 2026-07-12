from bot.llm.splitter import _naive_split, decide_parts


def test_decide_parts_thresholds():
    assert decide_parts("x" * 100) == 1
    assert decide_parts("x" * 700) == 2
    assert decide_parts("x" * 1500) == 3
    assert decide_parts("x" * 3000) == -1


def test_naive_split_count_and_nonempty():
    txt = "\n".join(f"خط {i} مبلغ {i}۰۰" for i in range(1, 10))
    parts = _naive_split(txt, 3)
    assert len(parts) == 3
    assert all(p.strip() for p in parts)
    # هیچ محتوایی گم نشود (تقریبی)
    joined = "".join(parts).replace(" ", "")
    assert "خط1" in joined and "خط9" in joined
