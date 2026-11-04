"""Единый формат выдачи.

Одна строка = одна сцена (пара картинка + видео). Номер «абзац.сцена» (12.3) — абзацы сквозные
по всему сценарию, без сброса на главах.

result.json (для сайта):
    {"project": {...}, "brief": {...}, "rows": [ROW, ...], "summary": {...}}
    ROW = {"number": "12.3", "paragraph_n": 12, "scene_n": 3, "shot": "ENVIRONMENT REACTION SHOT",
           "img": "<готовый промпт картинки>", "vid": "<готовый промпт видео>",
           "stock": ["<запрос>", ...]   — только у первой сцены абзаца,
           "paragraph": "<текст абзаца>", "problems": [...], "flags": [...]}

<название>.xlsx:
    лист «Prompts» — колонки как раньше у бота, чтобы ничего не сломать дальше по цепочке:
        number · img_prompt · vid_prompt · stock_query_1 · stock_query_2 · stock_query_3 · paragraph
    лист «Brief»   — эпоха, под-периоды, ведущий, персонажи, места, ограничения, абзацы (период, место,
                     что происходит);
    лист «Check»   — сцены с замечаниями проверки (что не исправилось после повторов) и пометками.
<название>.tsv — те же колонки, что «Prompts», для вставки в Google Sheets одним копированием.
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from . import brief as br
from .config import read_json, write_json

COLUMNS = ["number", "img_prompt", "vid_prompt", "stock_query_1", "stock_query_2", "stock_query_3", "paragraph"]


def rows(paragraphs: list[str], brief: dict[str, Any], scenes_dir: Path) -> list[dict[str, Any]]:
    out = []
    for n, text in enumerate(paragraphs, 1):
        rec = read_json(scenes_dir / f"{n}.json") or {}
        shots = rec.get("shots") or []
        scenes = rec.get("scenes") or []
        stock = br.stock_queries(brief, n)
        if not scenes:  # абзац так и не получил сцен — строка-заглушка, чтобы нумерация не сбилась
            out.append({"number": f"{n}.1", "paragraph_n": n, "scene_n": 1, "shot": "", "img": "", "vid": "",
                        "stock": stock, "paragraph": text, "problems": rec.get("problems") or ["нет ответа"],
                        "flags": []})
            continue
        problems = rec.get("problems") or []
        whole = [p for p in problems if not re.match(r"\d+\.\d+ ", p)]  # замечания ко всему абзацу — в 1-ю сцену
        for i, sc in enumerate(scenes, 1):
            mine = [p for p in problems if p.startswith(f"{n}.{i} ")] + (whole if i == 1 else [])
            out.append({"number": f"{n}.{i}", "paragraph_n": n, "scene_n": i,
                        "shot": shots[i - 1] if i - 1 < len(shots) else "",
                        "img": sc.get("img", ""), "vid": sc.get("vid", ""), "stock": stock if i == 1 else [],
                        "paragraph": text, "problems": mine,
                        "flags": [f for f in rec.get("flags") or [] if f.startswith(f"{n}.{i} ")]})
    return out


def summary(items: list[dict[str, Any]], paragraphs: list[str]) -> dict[str, Any]:
    return {"paragraphs": len(paragraphs), "scenes": len(items),
            "with_problems": sum(1 for r in items if r["problems"]),
            "with_flags": sum(1 for r in items if r["flags"]),
            "stock_queries": sum(len(r["stock"]) for r in items)}


def workbook(items: list[dict[str, Any]], brief: dict[str, Any]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Prompts"
    ws.append(COLUMNS)
    for r in items:
        st = (r["stock"] + ["", "", ""])[:3]
        ws.append([r["number"], r["img"], r["vid"], *st, r["paragraph"]])
    _style(ws, [8, 60, 60, 26, 26, 26, 80])

    wb_brief = wb.create_sheet("Brief")
    wb_brief.append(["field", "value"])
    for k, v in (("title", brief.get("title", "")), ("era", brief.get("era", "")),
                 ("synopsis", brief.get("synopsis", ""))):
        wb_brief.append([k, v])
    for p in brief.get("sub_periods", []):
        wb_brief.append([f"sub period [{p['id']}]", f"{p['name']} ({p['when']}). {p['description']} Material: "
                         f"{p['material']}. Palette: {p['palette']}. Lighting: {p['lighting']}. Textures: "
                         f"{p['textures']}."])
    host = brief.get("host")
    if host:
        wb_brief.append(["host clothing", host.get("clothing", "")])
        wb_brief.append(["host voice", host.get("voice", "")])
        for c in host.get("changes") or []:
            wb_brief.append([f"host clothing {c['from']}–{c['to']}", c["clothing"]])
    for c in brief.get("characters", []):
        wb_brief.append([f"character: {c['name']}", c["cue"] + (f" Likeness: {c['likeness']}" if c.get("likeness")
                                                                 else "")])
    for x in brief.get("locations", []):
        wb_brief.append([f"place [{x['id']}]", x["cue"]])
    for r in brief.get("restrictions", []):
        wb_brief.append([f"restriction ¶{r['paragraph']}", r["rule"]])
    wb_brief.append([])
    wb_brief.append(["paragraph", "sub period · place · what happens"])
    for p in brief.get("paragraphs", []):
        wb_brief.append([p["n"], f"[{p['period']}] {br.location_cue(brief, p['location'])} · {p['beat']}"])
    _style(wb_brief, [26, 140])

    chk = wb.create_sheet("Check")
    chk.append(["number", "shot", "problems (not fixed)", "notes"])
    for r in items:
        if r["problems"] or r["flags"]:
            chk.append([r["number"], r["shot"], "; ".join(r["problems"]), "; ".join(r["flags"])])
    _style(chk, [8, 30, 70, 70])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _style(ws: Any, widths: list[int]) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + i)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=False, vertical="top")


def tsv(items: list[dict[str, Any]]) -> str:
    def cell(v: Any) -> str:
        return str(v or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")

    lines = ["\t".join(COLUMNS)]
    for r in items:
        st = (r["stock"] + ["", "", ""])[:3]
        lines.append("\t".join(cell(x) for x in [r["number"], r["img"], r["vid"], *st, r["paragraph"]]))
    return "\n".join(lines) + "\n"


def safe_name(title: str) -> str:
    name = re.sub(r"[^\w\- ]+", "", title or "", flags=re.UNICODE).strip().replace(" ", "_")
    return (name or "prompts")[:80]


def write_all(project: dict[str, Any], paragraphs: list[str], brief: dict[str, Any], pdir: Path) -> dict[str, Any]:
    items = rows(paragraphs, brief, pdir / "scenes")
    summ = summary(items, paragraphs)
    out = pdir / "output"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():
        if old.is_file():
            old.unlink()
    base = safe_name(project.get("title") or "")
    suffix = "_avatar" if project.get("mode") == "avatar" else ""
    (out / f"{base}{suffix}.xlsx").write_bytes(workbook(items, brief))
    (out / f"{base}{suffix}.tsv").write_text(tsv(items), encoding="utf-8")
    write_json(pdir / "result.json", {"project": {k: project.get(k) for k in ("id", "title", "mode", "niche")},
                                      "brief": brief, "rows": items, "summary": summ})
    return summ
