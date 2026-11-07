from solo_leveling.interfaces.mcp.question_card import QuestionKeys


def test_saved_keys_survive_reopening_and_are_scoped_to_context(tmp_path):
    path = tmp_path / 'requests.sqlite3'
    keys = QuestionKeys(path)
    assert keys.recent('ctx') == []
    assert not path.exists()
    first = keys.reserve('ctx')['request_key']
    second = keys.reserve('ctx')['request_key']
    keys.reserve('other')
    reopened = QuestionKeys(path)
    assert [row['request_key'] for row in reopened.recent('ctx')] == [second, first]
    assert reopened.recent('unknown') == []
