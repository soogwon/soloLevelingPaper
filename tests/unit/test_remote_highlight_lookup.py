from solo_leveling.interfaces.http.remote_highlight_lookup import locate_quote


def test_quote_matches_copied_whitespace_and_preserves_offsets_and_candidates():
    original = "😀 Before. Observers recorded\n12 signals. Again: Observers recorded 12 signals."
    results = locate_quote([dict(chunk_id="chunk", pdf_page=1, section_id="s", original_text=original)],
                           {"s": "Methods"}, "Observers recorded   12 signals.")
    assert len(results) == 2
    assert results[0]["candidate_id"] != results[1]["candidate_id"]
    assert results[0]["section_title"] == "Methods"
    segment = results[0]["segments"][0]
    assert segment["start_offset"] == 10
    assert original[segment["start_offset"]:segment["end_offset"]] == segment["quote_snapshot"]
    assert segment["quote_snapshot"] == "Observers recorded\n12 signals."


def test_cross_chunk_quote_uses_verified_overlap_and_distinct_source_offsets():
    left = "Before testing, observers recorded 12 signals"
    right = "observers recorded 12 signals before calibration. Then they paused."
    rows = [
        dict(chunk_id="left", ordinal=0, pdf_page=1, section_id="methods", original_text=left),
        dict(chunk_id="right", ordinal=1, pdf_page=1, section_id="methods", original_text=right),
    ]
    quote = "testing, observers recorded 12 signals before calibration."
    candidate, = locate_quote(rows, {"methods": "Methods"}, quote)
    segments = candidate["segments"]
    assert len(segments) == 2
    assert "".join(s["quote_snapshot"] for s in segments) == quote
    for row, segment in zip(rows, segments, strict=True):
        assert row["original_text"][segment["start_offset"]:segment["end_offset"]] == segment["quote_snapshot"]
    assert len(locate_quote(rows, {}, "observers recorded 12 signals")) == 1
    assert locate_quote([rows[0], dict(rows[1], pdf_page=2)], {}, quote) == []
    assert locate_quote([rows[0], dict(rows[1], ordinal=3)], {}, quote) == []


def test_cross_chunk_does_not_join_repeated_or_unverifiable_overlap():
    rows = [
        dict(chunk_id="left", ordinal=0, pdf_page=1, section_id=None,
             original_text="before repeated wording repeated wording"),
        dict(chunk_id="right", ordinal=1, pdf_page=1, section_id=None,
             original_text="repeated wording repeated wording after"),
    ]
    assert locate_quote(rows, {}, "before repeated wording repeated wording after") == []
