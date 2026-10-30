"""EXP OCR parsing: the '[' between the EXP number and the percent is sometimes misread as '1' or
dropped, fusing the percent onto the number ('421766016.37' = 4217660 [6.37). EXP / percent is
constant within a level, so the processor learns that total from clean reads and picks the split
that matches it. Raw strings below are real OCR reads from a flat_sweep run (2026-09-24)."""
from exp_processor import ExpProcessor


def _learned():
    p = ExpProcessor()
    for raw in ("4154300[6.27", "4246460[6.4", "4404235[6.65", "4653410[7.0."):
        p.parse_exp_data(raw)
    return p


def test_clean_reads_parse_as_before():
    p = ExpProcessor()
    assert p.parse_exp_data("4154300[6.27")[0] == "4154300"
    assert p.parse_exp_data("2559545]3.86")[0] == "2559545"


def test_bracket_read_as_one_is_split_off():
    p = _learned()
    assert p.parse_exp_data("421766016.37")[0] == "4217660"
    assert p.parse_exp_data("466718517.0")[0] == "4667185"
    assert p.parse_exp_data("455198516.86")[0] == "4551985"


def test_bracket_dropped_is_split_off():
    p = _learned()
    assert p.parse_exp_data("46108356.9")[0] == "4610835"


def test_fused_read_before_any_clean_read_is_rejected_not_guessed():
    assert ExpProcessor().parse_exp_data("421766016.37") == (None, None)


def test_eight_digit_level_still_works():
    p = ExpProcessor()
    for raw in ("58995074[94.06", "58990000[94.05"):
        p.parse_exp_data(raw)
    assert p.parse_exp_data("5899507419406.")[0] in (None, "58995074")   # never the fused number
    assert p.parse_exp_data("589950741" + "94.06")[0] == "58995074"


def test_clean_read_percent_is_parsed_properly():
    p = ExpProcessor()
    assert p.parse_exp_data("4154300[6.27") == ("4154300", "6.27")
    assert p.parse_exp_data("58995074[94.06") == ("58995074", "94.06")
    assert p.parse_exp_data("4653410[7.0.") == ("4653410", "7.00")


def test_nine_digit_exp_needs_the_percent_in_the_box():
    # 2026-10-20 live reads: past 100,000,000 EXP the old OCR box ended at the '[' -> unparseable,
    # "last 10 min" sat at 0. The widened box (exp_tracking.EXP_OCR_REG) reads the whole percent.
    assert ExpProcessor().parse_exp_data("103122300[") == (None, None)
    assert ExpProcessor().parse_exp_data("103349960[96.65%]") == ("103349960", "96.65")
    assert ExpProcessor().parse_exp_data("103349960[96.65%") == ("103349960", "96.65")


def test_ocr_box_clears_the_percent():
    import exp_tracking
    assert exp_tracking.EXP_OCR_REG[2] >= 1260      # 1190 cut '[96.65%]' off a 9-digit EXP (live)
