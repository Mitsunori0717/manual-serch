from manualsearch.query import (
    build_match_expr,
    count_hits,
    find_spans,
    make_snippet,
    parse_query,
)


def test_terms_split_on_whitespace():
    parsed = parse_query("エラー 点滅")
    assert parsed.include == ["エラー", "点滅"]
    assert parsed.exclude == []


def test_quoted_japanese_phrase_is_collapsed_like_the_index():
    # 索引側でも日本語間の空白は落とすので、クエリも同じ形にそろえる
    assert parse_query('"冷却 ファン"').include == ["冷却ファン"]


def test_quoted_latin_phrase_keeps_its_space():
    assert parse_query('"cooling fan"').include == ["cooling fan"]


def test_minus_prefix_excludes():
    parsed = parse_query("ポンプ -旧型")
    assert parsed.include == ["ポンプ"]
    assert parsed.exclude == ["旧型"]


def test_fullwidth_minus_also_excludes():
    assert parse_query("ポンプ －旧型").exclude == ["旧型"]


def test_prolonged_sound_mark_is_not_a_minus():
    # ー(U+30FC) を除外記号と誤解しないこと
    assert parse_query("ーメーカー").include == ["ーメーカー"]


def test_query_is_normalized_like_the_index():
    assert parse_query("Ｅ２０３").include == ["E203"]


def test_short_and_long_terms_are_separated():
    parsed = parse_query("ポンプ 弁")
    assert parsed.fts_include == ["ポンプ"]
    assert parsed.like_include == ["弁"]


def test_match_expression_is_and_joined():
    assert build_match_expr(parse_query("エラー 表示灯")) == '"エラー" AND "表示灯"'


def test_match_expression_appends_not_clause():
    expr = build_match_expr(parse_query("ポンプ -旧型番"))
    assert expr == '"ポンプ" NOT ("旧型番")'


def test_short_exclusion_is_left_to_the_like_filter():
    # 2文字の除外語はtrigram索引で引けないのでMATCH式には現れない
    assert build_match_expr(parse_query("ポンプ -旧型")) == '"ポンプ"'


def test_match_expression_is_none_when_all_terms_are_short():
    assert build_match_expr(parse_query("弁")) is None


def test_double_quote_inside_term_is_escaped():
    assert build_match_expr(parse_query('あい"うえ')) == '"あい""うえ"'


def test_overlapping_spans_are_merged():
    assert find_spans("ええええ", ["ええ"]) == [(0, 4)]


def test_snippet_marks_every_term():
    snippet = make_snippet("冷却ファンとエラーコードE203", ["ファン", "E203"])
    assert "<mark>ファン</mark>" in snippet
    assert "<mark>E203</mark>" in snippet


def test_snippet_escapes_html():
    snippet = make_snippet("<script>ポンプ</script>", ["ポンプ"])
    assert "<script>" not in snippet
    assert "&lt;script&gt;" in snippet


def test_snippet_falls_back_to_head_when_nothing_matches():
    assert make_snippet("本文だけ", ["みつからない"]).startswith("本文だけ")


def test_count_hits_counts_all_occurrences():
    assert count_hits("ポンプとポンプ", ["ポンプ"]) == 2
