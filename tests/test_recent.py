from notes2gdoc import recent


def test_recent_docs_order_and_limit(tmp_path):
    path = tmp_path / "recent.json"
    for i in range(12):
        recent.remember(f"id{i}", f"Doc {i}", f"https://docs.google.com/document/d/id{i}/edit", path=path)
    recent.remember("id3", "Doc 3 renamed", "https://docs.google.com/document/d/id3/edit", path=path)
    items = recent.load(path)
    assert len(items) == recent.MAX_RECENT
    assert items[0].doc_id == "id3" and items[0].title == "Doc 3 renamed"
    assert [r.doc_id for r in items].count("id3") == 1
