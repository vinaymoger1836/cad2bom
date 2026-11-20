"""P0 end-to-end check (`make e2e`).

Brings the real stack up with Docker Compose, then drives it through the public front door
(Caddy -> API -> Postgres/procrastinate -> worker -> cad-worker) and checks:

  * a job is created with an unguessable link, and only a hash of its token is stored
  * a valid STEP upload is accepted and stored under data/jobs/<id>/in/
  * a program renamed .step, a native CAD file, an unknown type and an over-limit file are
    all refused with a clear message, and leave nothing behind
  * starting the job moves it to `extracting` and the cad-worker (a stub in P0) answers
  * the SSE job stream and the public demo stream work
  * both procrastinate queues (`pipeline`, `cad`) ran a job
  * the cad-worker really is sandboxed (no network, read-only root, 2 CPU / 3 GB)
  * git ignores .env, data/, caches and generated types

Set KEEP_UP=1 to leave the stack running afterwards. Exit code is non-zero on any failure.
"""

import hashlib
import json
import os
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

        stub: dict[str, object] | None = None
        kinds: list[str] = []
        for event, data in sse_events(c, f"{jobs}/events", auth, timeout=90):
            kinds.append(event)
            if event == "cad_stub":
                stub = json.loads(data)["data"]
                break
        check(stub is not None, "SSE delivered the cad-worker stub result", kinds)
        assert stub is not None
        check(
            stub.get("status") == "stub" and bool(stub.get("ocp_version")),
            "cad-worker answered from the sandbox (OpenCascade imported)",
            stub,
        )
        check("status" in kinds, "SSE carried status events", kinds)

        deadline = time.monotonic() + 30
        status = ""
        while time.monotonic() < deadline:
            status = c.get(jobs, headers=auth).json()["status"]
            if status == "extracting":
                break
            time.sleep(0.5)
        check(status == "extracting", "job reached `extracting`", status)

        queues = psql(
            "SELECT queue_name || ':' || status FROM procrastinate_jobs ORDER BY queue_name, status"
        ).splitlines()
        check(
            "pipeline:succeeded" in queues and "cad:succeeded" in queues,
            "both queues (pipeline, cad) ran a job",
            queues,
        )

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

    # --- git hygiene
    for path in [
        ".env",
        "data/jobs/x",
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
