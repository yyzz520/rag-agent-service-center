from app.rag import prompts


def test_registry_has_all_prompts_with_versions():
    assert set(prompts.versions()) == {
        "citation", "rewrite", "judge", "agent", "plan", "plan_synth",
    }
    for name, version in prompts.versions().items():
        assert version and prompts.get_prompt(name).text
        assert name in version


def test_versions_can_be_narrowed():
    assert prompts.versions(("citation", "judge")) == {
        "citation": "citation.v1",
        "judge": "judge.v1",
    }


def test_judge_prompt_keeps_format_slots():
    text = prompts.get_prompt("judge").text
    assert "{question}" in text and "{expected}" in text and "{answer}" in text
