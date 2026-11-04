import io

import pytest
from conftest import script_paragraphs, template_text
from docx import Document
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from promptgen.api.app import create_app
    from promptgen.api.settings import Settings

    return TestClient(create_app(Settings(api_token="secret", watch_every=0.1)))


H = {"Authorization": "Bearer secret"}


def test_token_required(client):
    assert client.get("/api/prompts/options").status_code == 401
    assert client.get("/health").status_code == 200


def test_options_and_niches(client):
    o = client.get("/api/prompts/options", headers=H).json()
    assert o["default_backend"] == "fake" or o["default_backend"]
    assert {b["id"] for b in o["backends"]} >= {"antigravity", "claude"}
    n = client.get("/api/prompts/niches", headers=H).json()
    assert n["scenario_online"] and n["niches"][0]["slug"] == "victorian"
    assert n["niches"][0]["templates"] == {"regular": None, "avatar": None}
    one = client.get("/api/prompts/niches/victorian", headers=H).json()
    assert "Audience" in one["passport"]


def test_template_upload_and_project_from_scenario(client):
    from promptgen import worker

    r = client.put("/api/prompts/niches/victorian/templates/avatar", headers=H,
                   json={"text": template_text("avatar"), "name": "VLOG_VICTORIAN_v2.txt"})
    assert r.status_code == 202 and r.json()["status"] == "queued"
    worker.run_tasks_once()
    t = client.get("/api/prompts/niches/victorian/templates/avatar", headers=H).json()
    assert t["status"] == "ready" and t["shots"] == ["PRIMARY BEAT", "FOLLOWUP BEAT"]

    scripts = client.get("/api/prompts/scripts?niche=victorian", headers=H).json()
    assert [s["id"] for s in scripts] == ["abc123"]  # черновики не показываем
    r = client.post("/api/prompts/projects", headers=H, json={
        "niche": "victorian", "mode": "avatar", "base": "template", "notes": "No children on screen.",
        "project_id": "roll-1", "script": {"source": "project", "id": "abc123"}})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["script"]["count"] == len(script_paragraphs()) and p["niche_name"] == "Victorian London"
    assert p["status"] == "queued" and p["stages"][0]["state"] == "todo"
    assert [x["id"] for x in client.get("/api/prompts/projects?project_id=roll-1", headers=H).json()] == [p["id"]]
    assert client.get("/api/prompts/projects?project_id=other", headers=H).json() == []
    worker.run_projects_once()
    v = client.get(f"/api/prompts/projects/{p['id']}", headers=H).json()
    assert v["status"] == "done", v["error"]
    res = client.get(f"/api/prompts/projects/{p['id']}/result", headers=H).json()
    assert res["summary"]["scenes"] == 2 * len(script_paragraphs()) and res["partial"] is False
    xlsx = next(f for f in v["files"] if f["name"].endswith(".xlsx"))
    f = client.get(f"/api/prompts/projects/{p['id']}/files/{xlsx['name']}", headers=H)
    assert f.status_code == 200 and f.content[:2] == b"PK"
    log = client.get(f"/api/prompts/projects/{p['id']}/log", headers=H).json()
    assert log["items"] and log["next"] == len(log["items"])
    assert client.get(f"/api/prompts/projects/{p['id']}/files/..%2Fproject.json", headers=H).status_code == 404


def test_upload_docx_script_and_passport_base(client):
    from promptgen import worker

    doc = Document()
    doc.add_paragraph("CHAPTER 1. The Night")
    for i, t in enumerate(script_paragraphs()[:5], 1):
        doc.add_paragraph(f"{i}. {t}")
    buf = io.BytesIO()
    doc.save(buf)
    r = client.post("/api/prompts/files/script", headers=H,
                    files={"file": ("victorian.docx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["count"] == 5 and not up["paragraphs"][0].startswith("1.")
    r = client.post("/api/prompts/projects", headers=H, json={
        "title": "Upload test", "niche": "victorian", "mode": "regular", "base": "passport", "pairs": 2,
        "script": {"source": "file", "name": up["name"], "paragraphs": up["paragraphs"]}})
    assert r.status_code == 201, r.text
    worker.run_projects_once()
    v = client.get(f"/api/prompts/projects/{r.json()['id']}", headers=H).json()
    assert v["status"] == "done" and v["template"]["builtin"]


def test_errors_are_readable(client):
    r = client.post("/api/prompts/projects", headers=H, json={
        "niche": "victorian", "mode": "regular", "base": "template",
        "script": {"source": "text", "name": "x", "text": "One paragraph.\n\nTwo paragraph."}})
    assert r.status_code == 422 and "шаблона" in r.json()["error"]["message"]
    assert client.get("/api/prompts/projects/nope", headers=H).status_code == 404
    r = client.post("/api/prompts/files/script", headers=H, files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 422


def test_stop_delete_and_brief_edit_guard(client):
    r = client.post("/api/prompts/projects", headers=H, json={
        "mode": "regular", "base": "passport", "pairs": 2,
        "script": {"source": "text", "name": "x", "text": "\n\n".join(script_paragraphs()[:3])}})
    pid = r.json()["id"]
    assert client.post(f"/api/prompts/projects/{pid}/stop", headers=H).json()["status"] == "stopped"
    assert client.put(f"/api/prompts/projects/{pid}/brief", headers=H, json={}).status_code == 409
    assert client.delete(f"/api/prompts/projects/{pid}", headers=H).status_code == 204
    assert client.get(f"/api/prompts/projects/{pid}", headers=H).status_code == 404


def test_sse_reports_project_change(client):
    """Генератор SSE с подставным запросом: изменение project.json даёт событие project.changed."""
    import asyncio

    from promptgen import project as prj
    from promptgen.api.routes import _sse

    r = client.post("/api/prompts/projects", headers=H, json={
        "mode": "regular", "base": "passport", "pairs": 2,
        "script": {"source": "text", "name": "x", "text": "First line here.\n\nSecond line here."}})
    pid = r.json()["id"]

    class FakeRequest:
        class app:
            class state:
                class settings:
                    watch_every = 0.05

        def __init__(self):
            self.n = 0

        async def is_disconnected(self):
            self.n += 1
            return self.n > 40

    async def collect():
        out = ""
        gen = _sse(FakeRequest())
        async for chunk in gen:
            out += chunk
            if "retry:" in chunk and "project" not in out:
                await asyncio.sleep(0.06)
                prj.change(pid, status="stopped")
            if "project.changed" in out:
                break
        return out

    seen = asyncio.run(collect())
    assert "project.changed" in seen and pid in seen
