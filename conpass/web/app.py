"""Local admin UI (FastAPI). Run: python -m conpass.web  -> http://127.0.0.1:8777"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from .. import paths, scheduler
from ..config import Reservation, load_reservations, save_reservations
from ..selectors import load_flow
from ..store import Store

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="777CON-PASS Auto")
_procs: dict[str, subprocess.Popen] = {}


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Block cross-site POSTs (any web page could otherwise POST to localhost)."""
    if request.method != "GET":
        src = request.headers.get("origin") or request.headers.get("referer") or ""
        host = urlparse(src).hostname
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return HTMLResponse("forbidden origin", status_code=403)
    return await call_next(request)


def _store() -> Store:
    return Store()


def _flash(msg: str) -> RedirectResponse:
    from urllib.parse import quote
    return RedirectResponse(f"/?msg={quote(msg)}", status_code=303)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, msg: str = "", edit: str = ""):
    err = ""
    try:
        rf = load_reservations()
    except (ValidationError, ValueError) as e:
        rf, err = None, f"reservations.yaml が不正です: {e}"
    st = _store()
    jobs = []
    for r in (rf.reservations if rf else []):
        last = st.runs(r.id, 1)
        state = st.get_state(r.dedupe_key())
        proc = _procs.get(r.id)
        jobs.append({
            "r": r, "scheduled": scheduler.is_installed(r.id), "last": last[0] if last else None,
            "state": state, "running": proc is not None and proc.poll() is None,
            "future": r.is_future(),
            "wake": scheduler.wake_command(r) if r.is_future() else "",
        })
    try:
        unverified = load_flow().unverified()
    except Exception as e:  # noqa: BLE001
        unverified = [f"selectors.yaml error: {e}"]
    editing = next((j["r"] for j in jobs if j["r"].id == edit), None)
    return TEMPLATES.TemplateResponse(request, "index.html", {
        "jobs": jobs, "runs": st.runs(limit=30), "msg": msg, "err": err,
        "unverified": unverified, "editing": editing,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.post("/jobs")
def save_job(id: str = Form(...), store: str = Form(...), visit_date: str = Form(...),
             execute_at: str = Form(...), timezone: str = Form("Asia/Tokyo"), mode: str = Form("test"),
             notify: str = Form(""), accept_from: str = Form(""), accept_until: str = Form(""),
             lead_seconds: int = Form(180), confirm_production: str = Form("")):
    if mode == "production" and confirm_production != "yes":
        return _flash("本番モードにするには確認チェックが必要です")
    data = dict(id=id.strip(), store=store.strip(), visit_date=visit_date, execute_at=execute_at,
                timezone=timezone, mode=mode, notify=bool(notify), lead_seconds=lead_seconds)
    if accept_from:
        data["accept_from"] = accept_from
    if accept_until:
        data["accept_until"] = accept_until
    try:
        new = Reservation.model_validate(data)
        rf = load_reservations()
        rf.reservations = [r for r in rf.reservations if r.id != new.id] + [new]
        rf = type(rf).model_validate(rf.model_dump())
    except (ValidationError, ValueError) as e:
        return _flash(f"入力エラー: {e}")
    save_reservations(rf)
    if scheduler.is_installed(new.id):  # keep launchd in sync with edited time
        scheduler.install(new)
    return _flash(f"保存しました: {new.id}")


@app.post("/jobs/{job_id}/delete")
def delete_job(job_id: str):
    scheduler.uninstall(job_id)
    rf = load_reservations()
    rf.reservations = [r for r in rf.reservations if r.id != job_id]
    save_reservations(rf)
    return _flash(f"削除しました: {job_id}（自動実行も解除）")


def _get(job_id: str) -> Reservation:
    for r in load_reservations().reservations:
        if r.id == job_id:
            return r
    raise HTTPException(404)


@app.post("/jobs/{job_id}/schedule")
def schedule_job(job_id: str):
    r = _get(job_id)
    try:
        p = scheduler.install(r)
    except Exception as e:  # noqa: BLE001
        return _flash(f"登録失敗: {e}")
    return _flash(f"launchd登録: {p.name} 起動 {scheduler.launch_time_local(r)}")


@app.post("/jobs/{job_id}/unschedule")
def unschedule_job(job_id: str):
    scheduler.uninstall(job_id)
    return _flash(f"自動実行を解除しました: {job_id}")


@app.post("/jobs/{job_id}/run")
def run_job(job_id: str, mode: str = Form("test"), confirm_production: str = Form("")):
    r = _get(job_id)
    if job_id in _procs and _procs[job_id].poll() is None:
        return _flash("実行中です")
    args = [sys.executable, "-m", "conpass.cli", "run", job_id]
    if mode == "test":
        args.append("--test")
    else:
        if r.mode != "production":
            return _flash("ジョブが本番モードではありません")
        if confirm_production != "yes":
            return _flash("本番実行には確認チェックが必要です")
    _procs[job_id] = subprocess.Popen(args, cwd=str(paths.ROOT), stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
    return _flash(f"{'テスト' if mode == 'test' else '本番'}実行を開始しました: {job_id}")


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int):
    import json
    row = _store().run(run_id)
    if not row:
        raise HTTPException(404)
    log = ""
    if row["log_path"] and Path(row["log_path"]).exists():
        log = Path(row["log_path"]).read_text(encoding="utf-8", errors="replace")[-60000:]
    shots = json.loads(row["screenshots"] or "[]")
    rel = [str(Path(s).resolve().relative_to(paths.DATA_DIR.resolve())) for s in shots
           if Path(s).resolve().is_relative_to(paths.DATA_DIR.resolve())]
    return TEMPLATES.TemplateResponse(request, "run.html", {"run": row, "log": log, "shots": rel})


@app.get("/files/{rel:path}")
def files(rel: str):
    p = (paths.DATA_DIR / rel).resolve()
    if not p.is_relative_to(paths.DATA_DIR.resolve()) or not p.is_file() or p.suffix not in (".png", ".xml", ".log", ".json"):
        raise HTTPException(404)
    return FileResponse(p)


def main():
    import uvicorn
    paths.ensure_dirs()
    uvicorn.run(app, host="127.0.0.1", port=8777)
