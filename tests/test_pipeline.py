import json

import openpyxl
from conftest import script_paragraphs


def _project(mode="regular", pairs=4, review=False, base="template", niche="victorian"):
    from promptgen import project as prj

    return prj.new_project(title="Victorian 283", niche=niche, niche_name="Victorian London", mode=mode, base=base,
                           pairs=pairs, notes="Keep the butler stern.", model="", models=None, review_brief=review,
                           script={"source": "file", "name": "v.txt"}, paragraphs=script_paragraphs())


def test_template_analysis_filters_placeholders_and_writer_rules(templates_ready):
    tpl = templates_ready
    reg = tpl.get("victorian", "regular")
    assert reg["status"] == "ready"
    a = reg["analysis"]
    joined = tpl.negatives_text(a)
    assert "[insert" not in joined
    assert "Downton" not in joined and "corporal punishment" not in joined
    assert any("не найден в шаблоне дословно" in w for w in reg["warnings"])  # выдуманный блок отброшен
    assert a["image_fixed"] and a["image_fixed"][0].startswith("cinematic historical realism")
    av = tpl.get("victorian", "avatar")["analysis"]
    assert len(av["shots"]) == 2 and av["spoken"]["max_chars"] == 80
    assert any(b.startswith("VLOG PERSPECTIVE") for b in av["image_fixed"])


def test_regular_run_end_to_end(templates_ready, tmp_path, monkeypatch):
    from promptgen import project as prj
    from promptgen import worker

    monkeypatch.setenv("FAKE_BAD", "3")  # абзац 3 в первый раз получит «blood» — должен переписаться
    p = _project("regular", pairs=4)
    worker.run_projects_once()
    v = prj.view(prj.load(p["id"]))
    assert v["status"] == "done", v["error"]
    assert [s["state"] for s in v["stages"]] == ["done"] * 4
    d = prj.pdir(p["id"])
    s3 = json.loads((d / "scenes" / "3.json").read_text(encoding="utf-8"))
    assert s3["attempts"] == 2 and not s3["problems"]
    assert all("blood" not in sc["img"] for sc in s3["scenes"])
    res = json.loads((d / "result.json").read_text(encoding="utf-8"))
    n = len(script_paragraphs())
    assert len(res["rows"]) == n * 4
    assert [r["number"] for r in res["rows"][:5]] == ["1.1", "1.2", "1.3", "1.4", "2.1"]
    row = res["rows"][0]
    assert ".." not in row["img"] and "Avoid: avoid" not in row["img"]
    assert row["img"].count("cinematic historical realism") == 1
    assert row["stock"] and row["stock"][0].endswith(", Late Victorian")
    assert res["rows"][1]["stock"] == []
    xlsx = next((d / "output").glob("*.xlsx"))
    wb = openpyxl.load_workbook(xlsx)
    assert wb.sheetnames == ["Prompts", "Brief", "Check"]
    head = [c.value for c in wb["Prompts"][1]]
    assert head == ["number", "img_prompt", "vid_prompt", "stock_query_1", "stock_query_2", "stock_query_3",
                    "paragraph"]
    assert wb["Prompts"].max_row == n * 4 + 1
    tsv = next((d / "output").glob("*.tsv")).read_text(encoding="utf-8")
    assert tsv.count("\n") == n * 4 + 1


def test_avatar_run_checks_clothing_and_batches(templates_ready):
    from promptgen import project as prj
    from promptgen import worker

    p = _project("avatar")
    assert p["pairs"] == 2
    worker.run_projects_once()
    v = prj.load(p["id"])
    assert v["status"] == "done", v["error"]
    assert v["usage"]["calls"] == 1 + 3  # бриф + 24 абзаца по 8 за вызов
    res = json.loads((prj.pdir(p["id"]) / "result.json").read_text(encoding="utf-8"))
    brief = res["brief"]
    clothing = brief["host"]["clothing"]
    for r in res["rows"]:
        assert r["img"].startswith("The host, exact appearance as the attached character sheet, " + clothing)
        assert "VLOG PERSPECTIVE (CRITICAL)" in r["img"] and "CONTINUITY:" in r["vid"]


def test_review_brief_then_launch_and_edit(templates_ready):
    from promptgen import project as prj
    from promptgen import worker

    p = _project("avatar", review=True)
    worker.run_projects_once()
    assert prj.load(p["id"])["status"] == "brief_ready"
    brief = json.loads((prj.pdir(p["id"]) / "brief.json").read_text(encoding="utf-8"))
    brief["host"]["clothing"] = "in her plain grey walking out dress and straw bonnet"
    prj.save_brief(p["id"], brief)
    prj.launch(p["id"])
    worker.run_projects_once()
    res = json.loads((prj.pdir(p["id"]) / "result.json").read_text(encoding="utf-8"))
    assert "plain grey walking out dress" in res["rows"][0]["img"]


def test_passport_base_uses_builtin_template(templates_ready):
    from promptgen import project as prj
    from promptgen import worker

    p = _project("regular", pairs=2, base="passport", niche="no-templates-niche")
    worker.run_projects_once()
    v = prj.load(p["id"])
    assert v["status"] == "done" and v["template"]["builtin"]


def test_template_base_without_template_fails_early():
    import pytest

    from promptgen import project as prj
    from promptgen.templates import TemplateError

    with pytest.raises(TemplateError):
        _project("regular", base="template", niche="empty")
    assert not list((prj.projects_dir()).glob("*")) if prj.projects_dir().exists() else True


def test_stop_and_resume_continue_from_saved(templates_ready, monkeypatch):
    from promptgen import project as prj
    from promptgen import worker
    from promptgen.generate import Generator

    p = _project("regular", pairs=2)
    calls = {"n": 0}
    orig = Generator._batch

    def stopping(self, nums, feedback, attempt, last):
        calls["n"] += 1
        if calls["n"] == 3:
            prj.change(p["id"], stop=True)
        return orig(self, nums, feedback, attempt, last)

    monkeypatch.setattr(Generator, "_batch", stopping)
    worker.run_projects_once()
    st = prj.load(p["id"])
    assert st["status"] == "stopped"
    done_before = len(list((prj.pdir(p["id"]) / "scenes").glob("*.json")))
    assert 0 < done_before < len(script_paragraphs())
    monkeypatch.setattr(Generator, "_batch", orig)
    prj.resume(p["id"])
    worker.run_projects_once()
    st = prj.load(p["id"])
    assert st["status"] == "done"
    assert st["usage"]["calls"] > 0


def test_limit_is_waited_out(templates_ready, monkeypatch):
    from promptgen import project as prj
    from promptgen import worker

    monkeypatch.setenv("FAKE_LIMIT_ON", "1")
    monkeypatch.setattr("promptgen.caller._sleep", lambda seconds, should_stop: None)  # не ждать по-настоящему
    from promptgen.backends.fake import FakeBackend

    FakeBackend.calls = 0
    p = _project("avatar")
    worker.run_projects_once()
    assert prj.load(p["id"])["status"] == "done"
    log = (prj.pdir(p["id"]) / "log.jsonl").read_text(encoding="utf-8")
    assert "Лимит" in log


def test_brief_missing_paragraph_is_requested_again(templates_ready, monkeypatch):
    from promptgen import project as prj
    from promptgen import worker

    monkeypatch.setenv("FAKE_BRIEF_SKIP", "5")
    p = _project("avatar")
    worker.run_projects_once()
    v = prj.load(p["id"])
    assert v["status"] == "done"
    brief = json.loads((prj.pdir(p["id"]) / "brief.json").read_text(encoding="utf-8"))
    assert len(brief["paragraphs"]) == len(script_paragraphs())
    assert brief["filled_by_program"] == [5]  # тестовый движок пропускает его и при дозапросе
