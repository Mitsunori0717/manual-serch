from manualsearch.textnorm import join_lines, normalize_blocks, normalize_line, normalize_text


def test_fullwidth_is_folded_to_halfwidth():
    assert normalize_line("エラーコード　Ｅ２０３") == "エラーコード E203"


def test_spaces_between_japanese_characters_are_removed():
    assert normalize_line("マ ニ ュ ア ル を 読 む") == "マニュアルを読む"


def test_spaces_around_latin_are_kept():
    assert normalize_line("型番 P-100 の設定") == "型番 P-100 の設定"


def test_japanese_line_break_joins_without_space():
    # 単語の途中で改行されても検索語として連結される
    assert join_lines(["エラーコ", "ードが表示"]) == "エラーコードが表示"


def test_english_hyphenated_line_break_joins():
    assert join_lines(["inter-", "national"]) == "international"


def test_english_lines_join_with_space():
    assert join_lines(["Replace the", "filter"]) == "Replace the filter"


def test_invisible_characters_are_dropped():
    assert normalize_line("ポン​プ") == "ポンプ"


def test_a_body_line_split_across_blocks_is_joined():
    # 1行が1ブロックとして返るPDFでも語が割れないよう、文の途中は連結する
    assert normalize_blocks(
        ["装置を停止してから冷却ファンのエラーコ", "ードE203を確認します。"]
    ) == "装置を停止してから冷却ファンのエラーコードE203を確認します。"


def test_a_short_block_is_treated_as_a_heading_and_kept_separate():
    # 見出しが本文に飲み込まれると章立てが拾えなくなる
    assert normalize_blocks(
        ["第2章 異常時の処置", "エラーコードE203が出たら電源を切ります。"]
    ) == "第2章異常時の処置\nエラーコードE203が出たら電源を切ります。"


def test_a_finished_sentence_is_not_glued_to_the_next_block():
    assert normalize_blocks(
        ["設置する前に必ずお読みください。", "資格を持つ作業者以外は分解しないでください。"]
    ) == "設置する前に必ずお読みください。\n資格を持つ作業者以外は分解しないでください。"


def test_non_japanese_block_boundary_keeps_a_newline():
    assert normalize_blocks(["Chapter 1", "Safety"]) == "Chapter 1\nSafety"


def test_normalize_text_drops_blank_lines():
    assert normalize_text("あ\n\n  \nい") == "あ\nい"
