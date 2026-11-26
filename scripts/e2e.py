"""End-to-end check (`make e2e`): P0 foundations, golden cases B3-B5 (P1-P6) and P7/P8 checks.

Brings the real stack up with Docker Compose, then drives it through the public front door
(Caddy -> API -> Postgres/procrastinate -> worker -> cad-worker) and checks:

  * a job is created with an unguessable link, and only a hash of its token is stored
  * a valid STEP upload is accepted and stored under data/jobs/<id>/in/
  * a program renamed .step, a native CAD file, an unknown type and an over-limit file are
    all refused with a clear message, and leave nothing behind
  * starting the job moves it to `extracting`; a STEP with no geometry ends cleanly
  * golden B3 (fan), B4 (supplier BoM file) and B5 (CAD + incomplete BoM) run through the real worker and cad-worker:
    draft EBOM/MBOM, suggestions, questions, GLB, blocked approval, then approval after review
  * the SSE job stream and the public demo stream work
  * both procrastinate queues (`pipeline`, `cad`) ran a job
  * the cad-worker really is sandboxed (no network, read-only root and input, 2 CPU / 3 GB)
  * git ignores .env, data/, caches and generated types
  * P8, stage 1 (Autodesk NOT configured): a native upload gets a clear message while STEP still works;
    the admin page is locked behind the reverse-proxy login and the passcode; failed logins are
    rate limited; delete-now removes a job everywhere; the library store is seeded
  * P8, stage 2 (the stack is recreated with Autodesk in replay mode): golden B7 - consent is
    required before any native upload, F3D -> STEP follows the B2 rules, SolidWorks properties
    and mesh features reach the BoM, the usage counter counts, the cap stops the next conversion;
    a job past its retention date is deleted by the sweep at start-up
  * P8, acceptance: a library edit published in the admin page changes a new job's result but not
    an existing job's pinned version

Set KEEP_UP=1 to leave the stack running afterwards. Exit code is non-zero on any failure.
"""

import csv
import hashlib
import io
import json
import os
import re
import struct
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    ".env",
    "-f",
    "infra/docker-compose.yml",
    "-f",
    "infra/docker-compose.e2e.yml",
]
# Stage 2 adds the Autodesk replay overlay (same project, so `up -d api worker` recreates them).
COMPOSE_APS = [*COMPOSE, "-f", "infra/docker-compose.e2e-aps.yml"]
BASIC = ("admin", "e2e-basic-pass")  # reverse-proxy login set in the e2e overlay (throwaway)
ADMIN_PASSCODE = "e2e-passcode"
METRICS_TOKEN = "e2e-metrics-token"  # noqa: S105  (throwaway test credential)
STEP_OK = (
    b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('e2e fixture'),'2;1');\n"
    b"FILE_NAME('e2e.step','2026-01-01T00:00:00',(''),(''),'','','');\n"
    b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\n"
    b"DATA;\nENDSEC;\nEND-ISO-10303-21;\n"
)
T0 = time.monotonic()


def say(msg: str) -> None:
    print(f"[{time.monotonic() - T0:6.1f}s] {msg}", flush=True)


class Failed(Exception):
    pass


def check(ok: bool, what: str, detail: object = "") -> None:
    if not ok:
        raise Failed(f"{what} {detail}".strip())
    say(f"PASS  {what}")


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        **kw,  # type: ignore[call-overload]
    )


def env_value(key: str, default: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or default
    return default


def psql(sql: str) -> str:
    user, db = env_value("POSTGRES_USER", "c2b"), env_value("POSTGRES_DB", "c2b")
    r = run([*COMPOSE, "exec", "-T", "postgres", "psql", "-U", user, "-d", db, "-tAc", sql])
    check(r.returncode == 0, f"psql ok: {sql[:50]}", r.stderr)
    return r.stdout.strip()


def sse_events(
    client: httpx.Client, path: str, headers: dict[str, str], timeout: float
) -> Iterator[tuple[str, str]]:
    """Yield (event, data) pairs from an SSE endpoint until the timeout."""
    deadline = time.monotonic() + timeout
    with client.stream("GET", path, headers=headers, timeout=timeout) as r:
        check(r.status_code == 200, f"SSE {path} opens", r.status_code)
        event, data = "message", ""
        for line in r.iter_lines():
            if time.monotonic() > deadline:
                return
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
            elif line == "" and data:
                yield event, data
                event, data = "message", ""


def main() -> int:
    say("preparing .env")
    r = run([sys.executable, "scripts/init_env.py"])
    print(r.stdout.strip())
    port = env_value("C2B_HTTP_PORT", "8080")
    base = f"http://localhost:{port}"

    say("docker compose up --build --wait (first run builds all images; this can take a while)")
    r = subprocess.run([*COMPOSE, "up", "-d", "--build", "--wait"], cwd=ROOT, check=False)
    check(r.returncode == 0, "stack is up and healthy")

    try:
        run_checks(base)
    except Failed as exc:
        say(f"FAIL  {exc}")
        print(run([*COMPOSE, "logs", "--tail=60"]).stdout[-6000:])
        return 1
    finally:
        if os.environ.get("KEEP_UP") == "1":
            say("KEEP_UP=1: leaving the stack running")
        else:
            say("tearing the stack down")
            run([*COMPOSE, "down", "-v"])
    say("ALL CHECKS PASSED")
    return 0


def wait_status(
    c: httpx.Client, jobs: str, auth: dict[str, str], want: set[str], timeout: float
) -> str:
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        status = c.get(jobs, headers=auth).json()["status"]
        if status in want:
            break
        time.sleep(1)
    return status


def new_job(
    c: httpx.Client,
    name: str,
    files: list[Path],
    description: str = "",
    volume: int | None = None,
) -> tuple[str, dict[str, str], str]:
    body: dict[str, object] = {"name": name}
    if description:
        body["description"] = description
    if volume is not None:
        body["annual_volume"] = volume
    r = c.post("/api/jobs", json=body)
    check(r.status_code == 201, f"[{name}] job created", r.text)
    job = r.json()
    auth = {"Authorization": f"Bearer {job['token']}"}
    jobs = f"/api/jobs/{job['id']}"
    for f in files:
        r = c.post(
            f"{jobs}/files",
            params={"filename": f.name},
            content=f.read_bytes(),
            headers={**auth, "Content-Type": "application/octet-stream"},
        )
        check(r.status_code == 201, f"[{name}] upload {f.name}", r.text)
    r = c.post(f"{jobs}/start", headers=auth)
    check(r.status_code == 202, f"[{name}] start accepted", r.text)
    return job["id"], auth, jobs


def resolve_everything(
    c: httpx.Client, jobs: str, auth: dict[str, str], answers: dict[str, str]
) -> None:
    """Answer every open question, accept every open suggestion, then approve."""
    bom = c.get(f"{jobs}/bom", headers=auth).json()
    for q in bom["questions"]:
        if q.get("answer") is None:
            ans = answers.get(q["id"]) or (q["options"][0] if q["options"] else "ok")
            r = c.post(f"{jobs}/questions/{q['id']}/answer", json={"answer": ans}, headers=auth)
            check(r.status_code == 200, f"answer {q['id']}", r.text)
    for sg in bom["suggestions"]:
        if sg.get("review") == "suggested":
            r = c.post(f"{jobs}/suggestions/{sg['id']}", json={"review": "accepted"}, headers=auth)
            check(r.status_code == 200, f"accept {sg['id']}", r.text)
    r = c.post(f"{jobs}/approve", headers=auth)
    check(
        r.status_code == 200 and r.json()["version"] == 1,
        "approval succeeds after review",
        r.text,
    )


def golden_b3(c: httpx.Client) -> None:
    g = ROOT / "golden" / "B3_fan"
    details = (g / "input" / "details.txt").read_text(encoding="utf-8").strip()
    _, auth, jobs = new_job(
        c, "B3 fan", [g / "input" / "fan.step"], description=details, volume=5000
    )
    status = wait_status(c, jobs, auth, {"review", "failed"}, 240)
    check(status == "review", "[B3] job reaches `review`", status)

    bom = c.get(f"{jobs}/bom", headers=auth).json()
    sizes = (len(bom["ebom"]), len(bom["mbom"]))
    check(sizes == (10, 10), "[B3] EBOM and MBOM have 10 lines", sizes)
    exp = json.loads((g / "expected" / "suggestions.json").read_text(encoding="utf-8"))
    got = [(x["id"], x["class"], x["rule"]) for x in bom["suggestions"]]
    check(
        got == [(x["id"], x["class"], x["rule"]) for x in exp],
        "[B3] gap suggestions match golden",
        got,
    )
    check(
        all(x["review"] == "suggested" for x in bom["suggestions"])
        and not any(ln.get("origin") == "gap_rule" for ln in bom["ebom"]),
        "[B3] nothing is added before a suggestion is accepted",
    )
    blade_q = [q for q in bom["questions"] if "blades" in q["text"]]
    check(
        len(blade_q) == 1 and "3 blades" in blade_q[0]["text"] and "says 4" in blade_q[0]["text"],
        "[B3] blade-count conflict (CAD 3 vs text 4) is a question",
        [q["text"] for q in bom["questions"]],
    )
    open_blocking = [q for q in bom["questions"] if q.get("blocking") and q.get("answer") is None]
    check(
        [q["id"] for q in open_blocking] == [blade_q[0]["id"]],
        "[B3] the blade question is the only blocking question (L1 is evidence only)",
        [q["id"] for q in open_blocking],
    )
    r = c.get(f"{jobs}/model.glb", headers=auth)
    check(
        r.status_code == 200 and r.content[:4] == b"glTF",
        "[B3] GLB model is served",
        r.status_code,
    )
    names = glb_node_names(r.content)
    check(
        any(re.fullmatch(r"N-\d{4}", n) for n in names),
        "[B3] GLB nodes are named with CAD node ids (the viewer links parts to lines by them)",
        sorted(names)[:8],
    )
    glb_ids = {re.sub(r"_\d+$", "", n) for n in names}  # the glTF loader numbers repeated names
    line_nodes = {n for ln in bom["ebom"] for n in ln.get("cad_nodes", [])}
    check(
        all(ln.get("cad_nodes") for ln in bom["ebom"]) and line_nodes <= glb_ids,
        "[B3] every EBOM line names its CAD nodes, and each one is a node in the GLB",
        sorted(line_nodes - glb_ids),
    )
    r = c.post(f"{jobs}/approve", headers=auth)
    check(
        r.status_code == 409 and r.json()["code"] == "approval_blocked",
        "[B3] approval is blocked while items are open",
        r.text,
    )
    resolve_everything(c, jobs, auth, {blade_q[0]["id"]: "3"})
    v = c.get(f"{jobs}/versions/1", headers=auth).json()
    check(
        len(v.get("ebom", [])) >= 10 and v.get("library_version") == bom["library_version"],
        "[B3] frozen version carries the library version and the lines",
        (len(v.get("ebom", [])), v.get("library_version")),
    )
    b3_exports(c, jobs, auth, v)
    b3_reopen_and_history(c, jobs, auth, v)


def glb_node_names(data: bytes) -> list[str]:
    """Names of the glTF nodes, read from the GLB's JSON chunk."""
    length, kind = struct.unpack_from("<II", data, 12)
    if kind != 0x4E4F534A:  # "JSON"
        return []
    doc = json.loads(data[20 : 20 + length])
    return [str(n.get("name", "")) for n in doc.get("nodes", [])]


def pv(x: object) -> object:
    """The plain value of a Prov dict or a plain value."""
    return x["value"] if isinstance(x, dict) and "value" in x else x


def b3_exports(c: httpx.Client, jobs: str, auth: dict[str, str], v: dict) -> None:
    """Every export format downloads and reconciles with the frozen BomVersion JSON."""
    from openpyxl import load_workbook

    def get(fmt: str, **extra: str) -> httpx.Response:
        r = c.get(f"{jobs}/versions/1/export", params={"fmt": fmt, **extra}, headers=auth)
        check(r.status_code == 200, f"[B3] export {fmt} {extra or ''} downloads", r.text[:200])
        return r

    check(get("json").json() == v, "[B3] JSON export equals GET /versions/1")
    schema = get("schema").json()
    check(
        set(v) <= set(schema["properties"]) and "ebom" in schema["properties"],
        "[B3] the JSON schema describes every key of the JSON export",
        sorted(set(v) - set(schema["properties"])),
    )

    r = get("xlsx")
    disposition = r.headers["content-disposition"]
    check(
        "attachment" in disposition and ".xlsx" in disposition,
        "[B3] XLSX is sent as a named download",
        disposition,
    )
    wb = load_workbook(io.BytesIO(r.content), data_only=True)
    want = [
        "EBOM",
        "MBOM",
        "Purchased items",
        "Open items",
        "Excluded from BoM",
        "Explanations",
        "About",
    ]
    check(wb.sheetnames == want, "[B3] XLSX has the sheets of docs/05 §3", wb.sheetnames)
    ws = wb["EBOM"]
    head = [str(x) for x in next(ws.iter_rows(max_row=1, values_only=True))]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    check(
        len(rows) == len(v["ebom"]),
        "[B3] XLSX EBOM has one row per line",
        (len(rows), len(v["ebom"])),
    )
    pn, qty = head.index("Part number"), head.index("Qty")
    got = sorted((str(r[pn] or ""), float(r[qty] or 0)) for r in rows)
    exp = sorted(
        (str(pv(ln.get("part_number")) or ""), float(pv(ln["qty_per_parent"]))) for ln in v["ebom"]
    )
    check(got == exp, "[B3] XLSX part numbers and quantities equal the version JSON")
    check(
        len(list(wb["MBOM"].iter_rows(min_row=2, values_only=True))) == len(v["mbom"]),
        "[B3] XLSX MBOM has one row per MBOM line",
    )
    buy_total = sum(m["qty_total"] for m in v["mbom"] if m["make_buy"] == "buy")
    pur = wb["Purchased items"]
    ph = [str(x) for x in next(pur.iter_rows(max_row=1, values_only=True))]
    got_buy = sum(
        float(r[ph.index("Qty per build")] or 0) for r in pur.iter_rows(min_row=2, values_only=True)
    )
    check(
        abs(got_buy - buy_total) < 1e-6,
        "[B3] purchased items add up to the bought MBOM quantity",
        (got_buy, buy_total),
    )

    for part in ("ebom", "mbom"):
        text = get("csv", part=part).content.decode("utf-8-sig")
        table = list(csv.reader(io.StringIO(text)))
        check(
            len(table) - 1 == len(v[part]) and "Level" in table[0] and "Parent" in table[0],
            f"[B3] CSV {part.upper()} has one row per line, with level and parent",
            (len(table) - 1, len(v[part])),
        )

    r = get("pdf")
    check(r.content[:5] == b"%PDF-", "[B3] PDF export is a PDF")
    import pdfplumber

    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        pdf_text = " ".join((p.extract_text() or "") for p in pdf.pages)
    check(
        "Generated BoM" in pdf_text and "review before release" in pdf_text,
        "[B3] PDF carries the 'Generated BoM - review before release' label",
    )
    check(
        all(str(pv(ln.get("part_number")) or "") in pdf_text for ln in v["ebom"]),
        "[B3] every part number of the version is in the PDF",
    )


def b3_reopen_and_history(c: httpx.Client, jobs: str, auth: dict[str, str], v: dict) -> None:
    r = c.post(f"{jobs}/approve", headers=auth)
    check(
        r.status_code == 200 and r.json()["version"] == 1 and r.json()["unchanged"] is True,
        "[B3] approving again without a change returns version 1, not a new one",
        r.text,
    )
    line = v["ebom"][0]
    before = str(pv(line["description"]))
    r = c.patch(
        f"{jobs}/lines/{line['id']}",
        json={"field": "description", "value": "B3 edited by e2e"},
        headers=auth,
    )
    check(r.status_code == 200, "[B3] a line edit after approval is accepted", r.text)
    check(
        c.get(jobs, headers=auth).json()["status"] == "review",
        "[B3] an edit after approval starts a new draft (status back to `review`)",
    )
    h = c.get(f"{jobs}/lines/{line['id']}/history", headers=auth).json()
    check(
        len(h) >= 1 and h[0]["before"] == before and h[0]["after"] == "B3 edited by e2e",
        "[B3] line history records before and after",
        h[:1],
    )
    ebom = c.get(f"{jobs}/bom", headers=auth).json()["ebom"]
    p = next(ln for ln in ebom if ln["id"] == line["id"])["description"]
    check(
        isinstance(p, dict) and p["source"] == "user" and p["confidence"] == 1.0,
        "[B3] the edit is source=user with confidence 1.0",
        p,
    )
    r = c.post(f"{jobs}/approve", headers=auth)
    check(
        r.status_code == 200 and r.json()["version"] == 2 and r.json()["unchanged"] is False,
        "[B3] approving the changed draft makes version 2",
        r.text,
    )
    vs = c.get(f"{jobs}/versions", headers=auth).json()
    check([x["version"] for x in vs] == [1, 2], "[B3] the version list shows both versions", vs)
    check(
        c.get(f"{jobs}/versions/1", headers=auth).json() == v,
        "[B3] version 1 is unchanged after version 2",
    )


def golden_b4(c: httpx.Client) -> None:
    """B4: a messy supplier BoM file, no CAD. One ambiguous column must be confirmed by a person."""
    g = ROOT / "golden" / "B4_power_bank"
    xlsx = g / "input" / "supplier_bom.xlsx"
    job_id, auth, jobs = new_job(c, "B4 power bank", [xlsx], description="Power bank assembly")
    status = wait_status(c, jobs, auth, {"review", "failed"}, 240)
    check(status == "review", "[B4] job reaches `review` without CAD", status)

    exp = json.loads((g / "expected" / "bom.json").read_text(encoding="utf-8"))["lines"]
    bom = c.get(f"{jobs}/bom", headers=auth).json()
    check(len(bom["ebom"]) == len(exp) == 14, "[B4] 14 user lines kept", len(bom["ebom"]))
    raws = [ln.get("raw") for ln in bom["ebom"]]
    check(raws == [x["raw"] for x in exp], "[B4] every user line is unchanged in `raw`")

    mq = [q for q in bom["questions"] if q.get("mapping_column")]
    check(
        len(mq) == 1 and mq[0]["mapping_column"] == "Item" and mq[0]["blocking"],
        "[B4] the ambiguous 'Item' column is a blocking question",
        [q["text"] for q in bom["questions"]],
    )
    check(mq[0].get("answer") is None, "[B4] nothing was confirmed without a person")
    r = c.get(f"{jobs}/mapping", headers=auth)
    item = r.json()["columns"]["Item"]
    check(
        r.status_code == 200 and item["by"] != "user" and not item["confirmed_by_user"],
        "[B4] mapping shows 'Item' as an unconfirmed proposal",
        item,
    )
    r = c.post(f"{jobs}/approve", headers=auth)
    check(
        r.status_code == 409,
        "[B4] approval is blocked until the column is confirmed",
        r.status_code,
    )

    r = c.post(f"{jobs}/mapping/confirm", json={"columns": {"Item": "nonsense"}}, headers=auth)
    check(r.status_code == 422, "[B4] an unknown field is refused", r.text)
    r = c.post(f"{jobs}/mapping/confirm", json={"columns": {"NoSuchCol": "notes"}}, headers=auth)
    check(r.status_code == 422, "[B4] an unknown column is refused", r.text)
    r = c.post(f"{jobs}/mapping/confirm", json={"columns": {"Item": "description"}}, headers=auth)
    check(
        r.status_code == 200 and r.json()["rebuild_needed"] is False, "[B4] confirm saved", r.text
    )
    item = c.get(f"{jobs}/mapping", headers=auth).json()["columns"]["Item"]
    check(
        item["by"] == "user" and item["confirmed_by_user"] is True,
        "[B4] 'Item' is now source=user",
        item,
    )
    saved = ROOT / "data" / "jobs" / job_id / "mapping_overrides.json"
    check(
        json.loads(saved.read_text(encoding="utf-8")) == {"Item": "description"},
        "[B4] the choice is stored per job",
    )
    resolve_everything(c, jobs, auth, {})


def golden_b5(c: httpx.Client) -> None:
    g = ROOT / "golden" / "B5_cad_plus_bom"
    job_id, auth, jobs = new_job(
        c,
        "B5 bracket + BoM",
        [g / "input" / "bracket_assembly.step", g / "input" / "user_bom.xlsx"],
    )
    status = wait_status(c, jobs, auth, {"review", "failed"}, 240)
    check(status == "review", "[B5] job reaches `review`", status)
    bom = c.get(f"{jobs}/bom", headers=auth).json()
    check(
        len(bom["suggestions"]) == 3,
        "[B5] exactly 3 'CAD shows X, add it?' suggestions",
        [x["id"] for x in bom["suggestions"]],
    )
    qty_q = [q for q in bom["questions"] if "your BoM says" in q["text"]]
    check(
        len(qty_q) == 1, "[B5] exactly 1 quantity question", [q["text"] for q in bom["questions"]]
    )
    llm = psql(f"SELECT count(*) FROM llm_call WHERE job_id = '{job_id}'")
    check(llm == "0", "[B5] zero LLM calls (rules only)", llm)
    r = c.post(f"{jobs}/approve", headers=auth)
    check(r.status_code == 409, "[B5] approval is blocked until resolved", r.status_code)
    resolve_everything(c, jobs, auth, {})


# --- P8 -----------------------------------------------------------------------------------------


def basic_get(c: httpx.Client, path: str, **kw: object) -> httpx.Response:
    return c.get(path, auth=BASIC, **kw)  # type: ignore[arg-type]


def admin_call(
    c: httpx.Client, method: str, path: str, token: str | None = None, **kw: object
) -> httpx.Response:
    """A call to /api/admin/*: the reverse-proxy login plus (when given) the session token."""
    headers = {"X-Admin-Token": token} if token else {}
    return c.request(method, f"/api/admin{path}", auth=BASIC, headers=headers, **kw)  # type: ignore[arg-type]


def native_unconfigured(c: httpx.Client) -> None:
    """With Autodesk unconfigured the STEP path works and native files get a clear answer."""
    r = c.post("/api/jobs", json={"name": "P8 native refusal"})
    job = r.json()
    auth = {"Authorization": f"Bearer {job['token']}"}
    jobs = f"/api/jobs/{job['id']}"
    r = c.patch(jobs, json={"consent_native_conversion": True}, headers=auth)
    check(
        r.status_code == 415 and r.json()["code"] == "native_not_configured",
        "[P8] consent to a conversion that is not set up is refused with a clear message",
        r.text,
    )
    r = c.post(
        f"{jobs}/files",
        params={"filename": "part.f3d"},
        content=b"PK\x03\x04" + b"\x00" * 64,
        headers={**auth, "Content-Type": "application/octet-stream"},
    )
    check(
        r.status_code == 415 and "isn't set up" in r.json()["detail"],
        "[P8] a .f3d is refused the same way",
        r.text,
    )
    r = c.post(
        f"{jobs}/files",
        params={"filename": "ok.step"},
        content=STEP_OK,
        headers={**auth, "Content-Type": "application/octet-stream"},
    )
    check(r.status_code == 201, "[P8] the STEP path still works next to the refusal", r.text)
    store = ROOT / "data" / "library"
    check(
        (store / "active").read_text(encoding="utf-8").strip() == "2026.11.1"
        and (store / "versions" / "2026.11.1" / "VERSION.yaml").is_file(),
        "[P8] the image's library was seeded as version 2026.11.1 in the store",
    )
    ready = c.get("/api/readyz").json()
    check("data_free_mb" in ready, "[P8] /readyz reports the data volume", ready)


def delete_now(c: httpx.Client) -> None:
    r = c.post("/api/jobs", json={"name": "P8 delete now"})
    job = r.json()
    jid = job["id"]
    auth = {"Authorization": f"Bearer {job['token']}"}
    jobs = f"/api/jobs/{jid}"
    c.post(
        f"{jobs}/files",
        params={"filename": "ok.step"},
        content=STEP_OK,
        headers={**auth, "Content-Type": "application/octet-stream"},
    )
    check((ROOT / "data" / "jobs" / jid).is_dir(), "[P8] the job has files on disk")
    r = c.delete(jobs)
    check(r.status_code == 401, "[P8] delete needs the job token", r.status_code)
    r = c.delete(jobs, headers=auth)
    check(
        r.status_code == 200 and r.json()["status"] == "deleted", "[P8] delete-now succeeds", r.text
    )
    check(not (ROOT / "data" / "jobs" / jid).exists(), "[P8] the job's files are gone")
    n = psql(f"SELECT count(*) FROM job WHERE id = '{jid}'")
    check(n == "0", "[P8] the job row (and its dependants) are gone", n)
    check(
        c.get(jobs, headers=auth).status_code == 401, "[P8] the old link no longer opens anything"
    )


def admin_checks(c: httpx.Client) -> str:
    """Two layers of protection, the session token, rate limiting, metrics. Returns a session token."""
    for path in ("/admin", "/api/admin/status", "/api/admin/library/versions"):
        r = c.get(path)
        check(r.status_code == 401, f"[P8] {path} needs the reverse-proxy login", r.status_code)
    r = basic_get(c, "/admin")
    check(
        r.status_code == 200 and 'id="root"' in r.text,
        "[P8] with the login the admin page is served",
    )
    r = admin_call(c, "GET", "/status")
    check(
        r.status_code == 401 and r.json()["code"] == "unauthorized",
        "[P8] the passcode is the second layer",
        r.text,
    )
    r = c.get("/api/admin/status", auth=("admin", "wrong"))
    check(r.status_code == 401, "[P8] a wrong web login is refused")

    r = c.post("/api/admin/login", auth=BASIC, json={"passcode": "nope"})
    check(r.status_code == 401, "[P8] a wrong passcode is refused", r.text)
    r = c.post("/api/admin/login", auth=BASIC, json={"passcode": ADMIN_PASSCODE})
    check(
        r.status_code == 200 and r.json()["token"],
        "[P8] the right passcode gives a session token",
        r.text,
    )
    token = r.json()["token"]

    r = admin_call(c, "GET", "/status", token)
    check(
        r.status_code == 200 and r.json()["library_active"] == "2026.11.1",
        "[P8] admin status shows the active library",
        r.text,
    )
    r = admin_call(c, "GET", "/library/versions", token)
    body = r.json()
    check(
        body["active"] == "2026.11.1" and [v["version"] for v in body["versions"]] == ["2026.11.1"],
        "[P8] one version is listed and active",
        body,
    )
    r = admin_call(
        c, "GET", "/library/versions/2026.11.1/file", token, params={"path": "../etc/passwd"}
    )
    check(r.status_code == 422, "[P8] a path outside the library is refused", r.status_code)
    r = admin_call(c, "GET", "/metrics", token)
    check(
        r.status_code == 200
        and 'c2b_jobs{status="review"}' in r.text
        and "c2b_aps_jobs_cap" in r.text,
        "[P8] metrics are served in Prometheus text",
        r.text[:200],
    )
    r = c.get("/api/admin/metrics", auth=BASIC, headers={"X-Metrics-Token": METRICS_TOKEN})
    check(r.status_code == 200, "[P8] a scraper can use the metrics token", r.status_code)

    # Rate limit last, so it cannot disturb the calls above: the login bucket allows 5 a minute.
    codes = [
        c.post("/api/admin/login", auth=BASIC, json={"passcode": "guess"}).status_code
        for _ in range(8)
    ]
    check(429 in codes, "[P8] repeated failed logins are rate limited (429)", codes)
    r = c.post("/api/admin/login", auth=BASIC, json={"passcode": "guess"})
    check(
        r.status_code == 429 and "Retry-After" in r.headers and r.json()["code"] == "rate_limited",
        "[P8] a 429 carries Retry-After and a clear message",
        r.text,
    )
    return token


def make_expired_job(c: httpx.Client) -> str:
    """A job whose retention date has passed; the sweep at the next api start deletes it."""
    r = c.post("/api/jobs", json={"name": "P8 expired"})
    job = r.json()
    auth = {"Authorization": f"Bearer {job['token']}"}
    c.post(
        f"/api/jobs/{job['id']}/files",
        params={"filename": "ok.step"},
        content=STEP_OK,
        headers={**auth, "Content-Type": "application/octet-stream"},
    )
    psql(f"UPDATE job SET expires_at = now() - interval '1 day' WHERE id = '{job['id']}'")
    check((ROOT / "data" / "jobs" / job["id"]).is_dir(), "[P8] the expired job still has files")
    return str(job["id"])


def wait_ready(base: str, timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/api/readyz", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise Failed("the api did not become ready after the restart")


def switch_to_aps(c: httpx.Client) -> None:
    say("recreating api + worker with Autodesk in replay mode (cap 3)")
    r = subprocess.run(
        [*COMPOSE_APS, "up", "-d", "--no-deps", "--force-recreate", "--wait", "api", "worker"],
        cwd=ROOT,
        check=False,
    )
    check(r.returncode == 0, "[P8] api and worker restarted with the replay overlay")
    wait_ready(str(c.base_url))
    cfg = c.get("/api/config").json()
    check(
        cfg["native_enabled"] is True
        and cfg["native_usage"] == {"used": 0, "cap": 3}
        and ".f3d" in cfg["accepted_extensions"]
        and "Autodesk" in cfg["native_consent_text"],
        "[P8] /config now offers native files, usage 0 of 3, with the consent wording",
        cfg,
    )


def check_expired_deleted(job_id: str) -> None:
    deadline = time.monotonic() + 60
    n = "1"
    while time.monotonic() < deadline:
        n = psql(f"SELECT count(*) FROM job WHERE id = '{job_id}'")
        if n == "0":
            break
        time.sleep(2)
    check(n == "0", "[P8] the retention sweep deleted the expired job", n)
    check(not (ROOT / "data" / "jobs" / job_id).exists(), "[P8] ... and its files")


def native_job(
    c: httpx.Client, name: str, filename: str, body: bytes
) -> tuple[str, dict[str, str], str]:
    """Create a job, check consent is required, give consent, upload, start. Returns id/auth/url."""
    r = c.post("/api/jobs", json={"name": name})
    job = r.json()
    auth = {"Authorization": f"Bearer {job['token']}"}
    jobs = f"/api/jobs/{job['id']}"
    headers = {**auth, "Content-Type": "application/octet-stream"}
    r = c.post(f"{jobs}/files", params={"filename": filename}, content=body, headers=headers)
    check(
        r.status_code == 409 and r.json()["code"] == "consent_required",
        f"[B7] {filename}: nothing native is stored before consent (409 consent_required)",
        r.text,
    )
    check(
        not (ROOT / "data" / "jobs" / job["id"] / "in").exists()
        or not any((ROOT / "data" / "jobs" / job["id"] / "in").iterdir()),
        f"[B7] {filename}: nothing was written to disk",
    )
    r = c.patch(jobs, json={"consent_native_conversion": True}, headers=auth)
    check(
        r.status_code == 200 and r.json()["consent_native_conversion"] is True,
        "[B7] consent recorded",
        r.text,
    )
    row = psql(
        "SELECT consent_native_conversion::text || '|' || (consent_native_at IS NOT NULL)::text || '|' "
        f"|| coalesce(consent_native_text_ver, '') FROM job WHERE id = '{job['id']}'"
    )
    check(row == "true|true|1", "[B7] consent is stored with a time and the wording version", row)
    r = c.post(f"{jobs}/files", params={"filename": filename}, content=body, headers=headers)
    check(r.status_code == 201, f"[B7] {filename}: accepted after consent", r.text)
    check(r.json()["kind"] == "cad", f"[B7] {filename} is stored as a CAD upload", r.json())
    r = c.post(f"{jobs}/start", headers=auth)
    check(r.status_code == 202, f"[B7] {filename}: start accepted", r.text)
    return job["id"], auth, jobs


def usage_used(c: httpx.Client) -> int:
    return int(c.get("/api/config").json()["native_usage"]["used"])


def golden_b7(c: httpx.Client) -> None:
    g = ROOT / "golden" / "B7_native"
    exp = json.loads((g / "expected" / "b7.json").read_text(encoding="utf-8"))

    # --- B7: Fusion F3D -> STEP -> the B2 rules
    jid, auth, jobs = native_job(
        c, "B7 Fusion", "bracket.f3d", (g / "input" / "bracket.f3d").read_bytes()
    )
    status = wait_status(c, jobs, auth, {"review", "failed"}, 240)
    check(status == "review", "[B7] the Fusion job reaches `review`", status)
    n = psql(
        f"SELECT count(*) FROM job_event WHERE job_id = '{jid}' AND kind = 'status' "
        "AND data->>'status' = 'converting'"
    )
    check(int(n) >= 1, "[B7] the job passed through `converting`", n)
    files = c.get(f"{jobs}/files", headers=auth).json()
    conv = [f for f in files if f["origin"] == "converted"]
    check(
        len(conv) == 1
        and conv[0]["detected_type"] == "step"
        and "converted" in conv[0]["filename"],
        "[B7] the converted STEP is listed as a converted upload",
        files,
    )
    check(
        any((ROOT / "data" / "converted" / jid).glob("*.step")),
        "[B7] the converted STEP is stored under data/converted/<job>/",
    )
    bom = c.get(f"{jobs}/bom", headers=auth).json()
    want = exp["f3d"]["downstream"]
    check(
        (len(bom["ebom"]), len(bom["mbom"])) == (want["ebom_lines"], want["mbom_lines"]),
        "[B7] the converted STEP follows the B2 rules (same EBOM and MBOM line counts)",
        (len(bom["ebom"]), len(bom["mbom"])),
    )
    check(usage_used(c) == 1, "[B7] the usage counter went from 0 to 1", usage_used(c))
    used = psql("SELECT jobs FROM aps_usage ORDER BY month DESC LIMIT 1")
    check(used == "1", "[B7] the counter is stored in the database", used)
    q = psql(
        "SELECT queue_name || ':' || status FROM procrastinate_jobs WHERE queue_name = 'native'"
    )
    check("native:succeeded" in q.splitlines(), "[B7] the `native` queue ran the conversion", q)

    # --- B7b: SolidWorks -> properties + meshes
    jid, auth, jobs = native_job(
        c, "B7 SolidWorks", "assembly.sldprt", (g / "input" / "assembly.sldprt").read_bytes()
    )
    status = wait_status(c, jobs, auth, {"review", "failed"}, 240)
    check(status == "review", "[B7] the SolidWorks job reaches `review`", status)
    check(usage_used(c) == 3, "[B7] a SolidWorks file counts 2 more (total 3)", usage_used(c))
    bom = c.get(f"{jobs}/bom", headers=auth).json()
    lines = {ln["cad_name"]: ln for ln in bom["ebom"] if ln.get("cad_name")}
    for part in exp["solidworks"]["parts"]:
        ln = lines.get(part["cad_name"])
        check(ln is not None, f"[B7] {part['cad_name']}: a BoM line exists", sorted(lines))
        assert ln is not None
        pn = ln.get("part_number")
        if part["part_number_from_cad"]:
            check(
                isinstance(pn, dict)
                and pn["value"] == part["part_number_from_cad"]
                and pn["source"] == "cad_meta"
                and "R-NUM-CAD" in pn["rules"],
                f"[B7] {part['cad_name']}: the part number comes from the CAD properties",
                pn,
            )
        if part["mass_g"] is not None:
            mass = ln["mass_g"]
            check(
                isinstance(mass, dict) and abs(mass["value"] - part["mass_g"]) < 1e-3,
                f"[B7] {part['cad_name']}: mass = mesh volume x library density ({part['mass_g']} g)",
                mass,
            )
            check(
                abs(mass["confidence"] - 0.9 * float(exp["solidworks"]["mesh_confidence_factor"]))
                < 1e-6
                and "R-MSS-MESH" in mass["rules"],
                f"[B7] {part['cad_name']}: mesh-based mass has lowered confidence",
                mass,
            )
    notice_events = psql(
        f"SELECT string_agg(data->>'code', ',') FROM job_event WHERE job_id = '{jid}' AND kind = 'notice'"
    )
    for code in exp["solidworks"]["notices"]:
        check(code in notice_events, f"[B7] the review shows the `{code}` notice", notice_events)
    r = c.get(f"{jobs}/model.glb", headers=auth)
    check(
        r.status_code == 200 and r.content[:4] == b"glTF",
        "[B7] the 3D model is served for the mesh parts",
    )
    check(
        any(re.fullmatch(r"N-\d{4}", nm) for nm in glb_node_names(r.content)),
        "[B7] GLB nodes carry CAD node ids",
    )

    # --- the cap: 3 of 3 used, so the next conversion is stopped with a clear message
    jid, auth, jobs = native_job(
        c, "B7 over the cap", "bracket.f3d", (g / "input" / "bracket.f3d").read_bytes()
    )
    status = wait_status(c, jobs, auth, {"review", "failed"}, 120)
    check(status == "failed", "[B7] past the monthly cap the job stops", status)
    reason = psql(
        f"SELECT message FROM job_event WHERE job_id = '{jid}' AND kind = 'notice' ORDER BY id DESC LIMIT 1"
    )
    check("monthly limit" in reason and "STEP" in reason, "[B7] the cap message is clear", reason)
    check(usage_used(c) == 3, "[B7] a refused conversion counts nothing", usage_used(c))


def total_mass(bom: dict) -> float:
    return round(
        sum(
            float(pv(ln["mass_g"]) or 0) * ln["qty_total"]
            for ln in bom["ebom"]
            if ln.get("cad_volume_mm3") is not None
        ),
        3,
    )


def library_acceptance(c: httpx.Client, token: str) -> None:
    """A library edit published in admin changes a new job's result, not an existing job's version."""
    step = ROOT / "golden" / "B2_bracket" / "input" / "bracket_assembly.step"
    _, old_auth, old_jobs = new_job(c, "P8 library before", [step])
    check(
        wait_status(c, old_jobs, old_auth, {"review", "failed"}, 240) == "review",
        "[P8] job before reaches review",
    )
    old_bom = c.get(f"{old_jobs}/bom", headers=old_auth).json()
    old_version = c.get(old_jobs, headers=old_auth).json()["library_version"]
    check(old_version == "2026.11.1", "[P8] the first job is pinned to 2026.11.1", old_version)

    # draft -> edit -> validate -> publish -> diff
    r = admin_call(c, "POST", "/library/drafts", token, json={"from_version": old_version})
    check(r.status_code == 201, "[P8] a draft is started from the active version", r.text)
    draft = r.json()["id"]
    r = admin_call(
        c, "GET", f"/library/drafts/{draft}/file", token, params={"path": "materials.yaml"}
    )
    text = r.json()["content"]
    new_text = re.sub(r"(code: steel_crca,[^\n]*density_g_cm3: )7\.85", r"\g<1>7.90", text, count=1)
    check(new_text != text, "[P8] the draft edit applies (steel_crca density 7.85 -> 7.90)")
    r = admin_call(
        c,
        "PUT",
        f"/library/drafts/{draft}/file",
        token,
        params={"path": "materials.yaml"},
        json={"content": "a: [broken"},
    )
    check(r.status_code == 422, "[P8] invalid YAML is refused when saving", r.text)
    r = admin_call(
        c,
        "PUT",
        f"/library/drafts/{draft}/file",
        token,
        params={"path": "materials.yaml"},
        json={"content": new_text},
    )
    check(r.status_code == 200, "[P8] the edited file is saved to the draft", r.text)
    r = admin_call(c, "POST", f"/library/drafts/{draft}/validate", token)
    check(r.status_code == 200 and r.json()["ok"] is True, "[P8] the draft validates", r.text)
    r = admin_call(
        c, "GET", "/library/diff", token, params={"a": old_version, "b": f"draft:{draft}"}
    )
    d = r.json()
    check(
        d["files_changed"] == 1 and d["changes"][0]["file"] == "materials.yaml",
        "[P8] the diff shows the one changed file",
        d["changes"][:1],
    )
    r = admin_call(c, "POST", f"/library/drafts/{draft}/publish", token, json={"changelog": []})
    check(r.status_code == 422, "[P8] publishing needs a changelog", r.text)
    r = admin_call(
        c,
        "POST",
        f"/library/drafts/{draft}/publish",
        token,
        json={"changelog": ["steel_crca density 7.90 (e2e)"]},
    )
    check(
        r.status_code == 200 and r.json()["version"] == "2026.11.2",
        "[P8] published as 2026.11.2",
        r.text,
    )
    r = admin_call(c, "GET", "/library/versions", token)
    check(
        r.json()["active"] == "2026.11.2" and len(r.json()["versions"]) == 2,
        "[P8] 2026.11.2 is now active",
    )
    check(
        "2026.11.2" in psql("SELECT version FROM library_version").splitlines(),
        "[P8] the new version was also recorded in the database",
        psql("SELECT version FROM library_version ORDER BY version"),
    )

    # the existing job keeps its version; a new job gets the new one
    check(
        c.get(old_jobs, headers=old_auth).json()["library_version"] == old_version,
        "[P8] the existing job is still pinned to 2026.11.1",
    )
    _, new_auth, new_jobs = new_job(c, "P8 library after", [step])
    check(
        wait_status(c, new_jobs, new_auth, {"review", "failed"}, 240) == "review",
        "[P8] job after reaches review",
    )
    new_bom = c.get(f"{new_jobs}/bom", headers=new_auth).json()
    check(
        c.get(new_jobs, headers=new_auth).json()["library_version"] == "2026.11.2"
        and new_bom["library_version"] == "2026.11.2",
        "[P8] the new job is pinned to 2026.11.2",
    )
    m_old, m_new = total_mass(old_bom), total_mass(new_bom)
    check(m_new > m_old, "[P8] the new job's result changed (total mass is higher)", (m_old, m_new))

    # working on the old job afterwards still uses its pinned version
    lines = [ln for ln in old_bom["ebom"] if ln.get("cad_volume_mm3") is not None]
    r = c.patch(
        f"{old_jobs}/lines/{lines[0]['id']}",
        json={"field": "description", "value": "edited"},
        headers=old_auth,
    )
    check(r.status_code == 200, "[P8] editing the old job works", r.text)
    after = c.get(f"{old_jobs}/bom", headers=old_auth).json()
    check(
        after["library_version"] == old_version and total_mass(after) == m_old,
        "[P8] ... and the old job's result and version are unchanged",
        (after["library_version"], total_mass(after), m_old),
    )


def run_checks(base: str) -> None:
    with httpx.Client(base_url=base, timeout=30) as c:
        # --- front door and web build
        r = c.get("/")
        check(r.status_code == 200 and 'id="root"' in r.text, "web app is served by Caddy")
        check("script-src 'self'" in r.headers.get("content-security-policy", ""), "CSP header set")
        r = c.get("/api/healthz")
        check(r.status_code == 200 and r.json() == {"status": "ok"}, "GET /api/healthz")
        r = c.get("/api/readyz")
        check(r.status_code == 200, "GET /api/readyz (database reachable)")
        cfg = c.get("/api/config").json()
        check(cfg["max_cad_mb"] == 1, "e2e overlay applied (CAD limit is 1 MB)", cfg)

        # --- job creation and token handling
        r = c.post("/api/jobs", json={"name": "E2E widget", "annual_volume": 500})
        check(r.status_code == 201, "POST /api/jobs creates a job", r.text)
        job = r.json()
        job_id, token = job["id"], job["token"]
        check(job["link"] == f"/j/{job_id}#{token}", "link carries the token in the fragment")
        check(len(token) >= 40, "token has at least 256 bits of entropy", len(token))
        auth = {"Authorization": f"Bearer {token}"}
        jobs = f"/api/jobs/{job_id}"

        r = c.get(jobs)
        check(r.status_code == 401, "no token is refused", r.status_code)
        check("access key" in r.json()["detail"], "refusal message is clear", r.json())
        r = c.get(jobs, headers={"Authorization": "Bearer wrong"})
        check(r.status_code == 401, "wrong token is refused")
        r = c.get(jobs, headers=auth)
        check(
            r.status_code == 200 and r.json()["status"] == "uploading", "job is readable with token"
        )

        stored_hash = psql(f"SELECT encode(token_hash, 'hex') FROM job WHERE id = '{job_id}'")
        check(
            stored_hash == hashlib.sha256(token.encode()).hexdigest(), "DB stores SHA-256 of token"
        )
        check(
            token not in psql(f"SELECT row_to_json(j)::text FROM job j WHERE id = '{job_id}'"),
            "raw token is not stored anywhere in the job row",
        )

        # --- uploads: accepted
        def upload(name: str, body: bytes) -> httpx.Response:
            return c.post(
                f"{jobs}/files",
                params={"filename": name},
                content=body,
                headers={**auth, "Content-Type": "application/octet-stream"},
            )

        r = upload("bracket.step", STEP_OK)
        check(r.status_code == 201, "valid STEP upload is accepted", r.text)
        up = r.json()
        check(up["kind"] == "cad" and up["detected_type"] == "step", "detected as CAD / step", up)
        check(up["sha256"] == hashlib.sha256(STEP_OK).hexdigest(), "sha256 recorded matches")
        in_dir = ROOT / "data" / "jobs" / job_id / "in"
        stored = in_dir / f"{up['id']}.step"
        check(
            stored.is_file() and stored.read_bytes() == STEP_OK,
            "file stored under data/jobs/<id>/in/",
        )

        # --- uploads: refused with clear messages
        r = upload("bracket2.step", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 1000)
        d = r.json().get("detail", "")
        check(
            r.status_code == 415 and r.json()["code"] == "type_mismatch",
            "program renamed .step is refused (415)",
            r.text,
        )
        check(
            "Windows program" in d and "ISO-10303-21" in d,
            "message says what it is and what was expected",
            d,
        )

        r = upload("huge.step", STEP_OK + b"/* pad */ " * 250_000)  # about 2.5 MB, limit is 1 MB
        check(
            r.status_code == 413 and r.json()["code"] == "file_too_large",
            "over-limit file is refused (413)",
            r.text,
        )
        check(
            "limit for CAD files is 1 MB" in r.json()["detail"],
            "message states the limit",
            r.json(),
        )

        r = upload("model.sldprt", b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
        check(
            r.status_code == 415
            and r.json()["code"] == "native_not_configured"
            and "export it as STEP" in r.json()["detail"]
            and "isn't set up" in r.json()["detail"],
            "native CAD with Autodesk unconfigured gets a clear message (415 native_not_configured)",
            r.text,
        )
        check(
            c.get("/api/config").json()["native_enabled"] is False,
            "/config says native conversion is off",
        )
        r = upload("setup.exe", b"MZ" + b"\x00" * 100)
        check(
            r.status_code == 415 and "not accepted" in r.json()["detail"],
            "unknown type is refused",
            r.text,
        )
        r = upload("notes.pdf", b"MZ\x90\x00" + b"\x00" * 100)
        check(r.status_code == 415, "exe renamed .pdf is refused", r.text)
        r = upload("empty.step", b"")
        check(r.status_code == 422, "empty file is refused", r.text)

        leftovers = sorted(p.name for p in in_dir.iterdir())
        check(leftovers == [stored.name], "refused uploads left nothing on disk", leftovers)
        r = c.get(f"{jobs}/files", headers=auth)
        check(len(r.json()) == 1, "only the valid file is listed", r.json())

        # --- start -> extracting, SSE, queues
        r = c.post(f"{jobs}/start", headers=auth)
        check(r.status_code == 202, "POST /start accepted", r.text)
        r = c.post(f"{jobs}/start", headers=auth)
        check(r.status_code == 422, "starting twice is refused", r.status_code)
        r = upload("late.step", STEP_OK)
        check(r.status_code == 422, "uploads are closed once building starts", r.status_code)

        statuses: list[str] = []
        for event, data in sse_events(c, f"{jobs}/events", auth, timeout=90):
            if event == "status":
                st = json.loads(data)["data"].get("status")
                statuses.append(str(st))
                if st in {"failed", "review"}:
                    break
        check("extracting" in statuses, "SSE reported `extracting`", statuses)
        status = wait_status(c, jobs, auth, {"failed", "review"}, 60)
        check(status in {"failed", "review"}, "a STEP without geometry ends cleanly", status)

        queues = psql(
            "SELECT queue_name || ':' || status FROM procrastinate_jobs ORDER BY queue_name, status"
        ).splitlines()
        check(
            "pipeline:succeeded" in queues and "cad:succeeded" in queues,
            "both queues (pipeline, cad) ran a job",
            queues,
        )

        # --- golden cases through the real worker and cad-worker
        golden_b3(c)
        golden_b4(c)
        golden_b5(c)

        # --- P8 stage 1: Autodesk not configured, admin, delete-now, library store
        native_unconfigured(c)
        delete_now(c)
        admin_token = admin_checks(c)
        expired_job = make_expired_job(c)

        # --- P8 stage 2: recreate api + worker with Autodesk in replay mode
        switch_to_aps(c)
        check_expired_deleted(expired_job)
        golden_b7(c)
        library_acceptance(c, admin_token)

        # --- demo stream
        ticks = [e for e, _ in sse_events(c, "/api/demo/stream", {}, timeout=20)]
        check(
            ticks.count("progress") == 10 and ticks[-1] == "done",
            "demo SSE stream sends 10 progress events then done",
            ticks,
        )

    # --- sandbox properties of the cad-worker container
    cid = run([*COMPOSE, "ps", "-q", "cad-worker"]).stdout.strip()
    fmt = "{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}|{{.HostConfig.Memory}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.PidsLimit}}"
    info = run(["docker", "inspect", "-f", fmt, cid]).stdout.strip().split("|")
    check(info[0] == "none", "cad-worker has no network", info)
    check(info[1] == "true", "cad-worker root filesystem is read-only", info)
    check(info[2] == str(3 * 1024**3), "cad-worker RAM limit is 3 GB", info)
    check(info[3] == "2000000000", "cad-worker CPU limit is 2", info)
    uid = run([*COMPOSE, "exec", "-T", "cad-worker", "id", "-u"]).stdout.strip()
    check(uid != "0", "cad-worker runs as a non-root user", uid)
    net = run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "cad-worker",
            "python",
            "-c",
            "import socket;socket.create_connection(('postgres',5432),2)",
        ]
    )
    check(net.returncode != 0, "cad-worker cannot reach the database or anything else")
    ro = run([*COMPOSE, "exec", "-T", "cad-worker", "sh", "-c", "touch /data/jobs/.w 2>&1"])
    check(ro.returncode != 0, "cad-worker input mount is read-only", ro.stdout)
    rw = run([*COMPOSE, "exec", "-T", "cad-worker", "sh", "-c", "touch /out/.w && rm /out/.w"])
    check(rw.returncode == 0, "cad-worker can write results to its output mount only", rw.stderr)

    # --- git hygiene
    for path in [
        ".env",
        "data/jobs/x",
        "data/out/x/model.glb",
        "out/B1/model.glb",
        "golden/B1/out/model.glb",
        "packages/ts-types/index.d.ts",
        "node_modules/x",
        ".venv/x",
        "apps/web/dist/index.html",
        "x/__pycache__/y.pyc",
        "backups/20260101T000000Z/db.sql.gz",
        "data/library/active",
        "data/converted/x/part.step",
        "aps-live/x.json",
        ".mypy_cache/x",
        ".ruff_cache/x",
        "server.log",
    ]:
        ig = run(["git", "check-ignore", "-q", path])
        check(ig.returncode == 0, f"git ignores {path}")


if __name__ == "__main__":
    sys.exit(main())
