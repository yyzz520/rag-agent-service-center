from scripts.seed_demo import sample_files


def test_sample_files_excludes_helpers_and_readme():
    names = {p.name for p in sample_files()}
    assert "清源缓释片说明书.pdf" in names
    assert "_gen_corpus.py" not in names
    assert "README.md" not in names
