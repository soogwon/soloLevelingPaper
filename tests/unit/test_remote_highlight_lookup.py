from solo_leveling.interfaces.http.remote_highlight_lookup import locate_quote


def test_quote_matches_copied_whitespace_and_preserves_offsets_and_candidates():
    original = "😀 Before. Observers recorded\n12 signals. Again: Observers recorded 12 signals."
    results = locate_quote([dict(chunk_id="chunk", pdf_page=1, section_id="s", original_text=original)],
                           {"s": "Methods"}, "Observers recorded   12 signals.")
    assert len(results) == 2
    assert results[0]["candidate_id"] != results[1]["candidate_id"]
    assert results[0]["section_title"] == "Methods"
    segment = results[0]["segment"]
    assert segment["start_offset"] == 10
    assert original[segment["start_offset"]:segment["end_offset"]] == segment["quote_snapshot"]
    assert segment["quote_snapshot"] == "Observers recorded\n12 signals."
