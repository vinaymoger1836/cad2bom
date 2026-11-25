"""End-to-end check (`make e2e`): P0 foundations plus the golden cases B3 and B5 (P1-P6).

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
            r.status_code == 415 and "export it as STEP" in r.json()["detail"],
            "native CAD gets a helpful refusal",
            r.text,
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
        ".mypy_cache/x",
        ".ruff_cache/x",
        "server.log",
    ]:
        ig = run(["git", "check-ignore", "-q", path])
        check(ig.returncode == 0, f"git ignores {path}")


if __name__ == "__main__":
    sys.exit(main())
