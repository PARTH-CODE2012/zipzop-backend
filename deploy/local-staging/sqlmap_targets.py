"""sqlmap against the endpoints that take free-form input into a query.

docs/07-security.md §7 asks for it on "a few endpoints". These are the ones
where a client-controlled string reaches the database layer: the login email,
the two opaque pagination cursors, and the job filters. Every query is built
by SQLAlchemy and semgrep agrees, so this is confirmation, not discovery.

Paced under the rate limits on purpose — a run that collects 429s instead of
answers proves nothing. Prints one `SQLMAP <target>: <verdict>` line each.
"""

import os
import subprocess
import uuid

import httpx

API = "https://api.zipzop.test"
CA = os.environ["CA_BUNDLE"]

with httpx.Client(verify=CA, timeout=30) as http:
    registered = http.post(
        f"{API}/v1/auth/register",
        json={
            "email": f"sqlmap-{uuid.uuid4().hex[:8]}@example.com",
            "password": "staging-pass-1234",
        },
    )
    assert registered.status_code == 201, f"could not open an account: {registered.status_code}"
    token = registered.json()["accessToken"]
    # A real project, so the job filter is answered rather than 404'd.
    project = http.post(
        f"{API}/v1/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "sqlmap"},
    ).json()["id"]
bearer = f"Authorization: Bearer {token}"

TARGETS = [
    # (name, extra sqlmap arguments, delay in seconds)
    (
        "login",
        # 401 is the login's ordinary answer to a wrong password, not a refusal
        # to be scanned — without `--ignore-code` sqlmap stops at the first one.
        [
            "-u",
            f"{API}/v1/auth/login",
            "--data",
            '{"email":"a@example.com*","password":"x*"}',
            "--method",
            "POST",
            "-H",
            "Content-Type: application/json",
            "--ignore-code",
            "401",
        ],
        3.2,  # 20 a minute
    ),
    ("media cursor", ["-u", f"{API}/v1/media?cursor=abc*&limit=10*", "-H", bearer], 0.7),
    ("ledger cursor", ["-u", f"{API}/v1/credits/ledger?cursor=abc*&limit=10*", "-H", bearer], 0.7),
    (
        "job filters",
        ["-u", f"{API}/v1/jobs?status=running*&projectId={project}*", "-H", bearer],
        0.7,
    ),
]

for name, args, delay in TARGETS:
    run = subprocess.run(
        [
            "sqlmap",
            *args,
            "--batch",
            "--level",
            "1",
            "--risk",
            "1",
            "--dbms",
            "PostgreSQL",
            "--technique",
            "BEUS",
            "--delay",
            str(delay),
            "--timeout",
            "15",
            "--retries",
            "1",
            "--disable-coloring",
            "--output-dir",
            "/tmp/sqlmap",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "REQUESTS_CA_BUNDLE": CA},
        check=False,
    )
    out = run.stdout
    if "is vulnerable" in out or "sqlmap identified the following injection point" in out:
        verdict = "INJECTABLE — see the log"
    elif "all tested parameters do not appear to be injectable" in out:
        verdict = "no injection found"
    else:
        verdict = "inconclusive — see the log"
    print(out[-3000:])
    print(f"SQLMAP {name}: {verdict}", flush=True)
