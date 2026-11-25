from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / ".claude" / "skills"
REQUIRED_SECTIONS = ("何时使用", "执行步骤", "输出契约", "失败处理")


def _skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").exists())


def _meta(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md 必须以 YAML frontmatter 开头"
    end = text.index("\n---", 4)
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta


def test_skills_have_frontmatter_and_name_matches_dir():
    dirs = _skill_dirs()
    assert {p.name for p in dirs} >= {"enterprise-kb-qa", "warehouse-batch-status"}
    for skill_dir in dirs:
        meta = _meta((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        assert meta.get("name") == skill_dir.name
        assert len(meta.get("description", "")) >= 10


def test_skills_document_contract_and_failure_paths():
    for skill_dir in _skill_dirs():
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        for section in REQUIRED_SECTIONS:
            assert section in text, f"{skill_dir.name} 缺少 {section}"


def test_skill_scripts_exist_and_compile():
    for skill_dir in _skill_dirs():
        scripts = sorted((skill_dir / "scripts").glob("*.py"))
        assert scripts, f"{skill_dir.name} 缺少演示脚本"
        for script in scripts:
            compile(script.read_text(encoding="utf-8"), str(script), "exec")
