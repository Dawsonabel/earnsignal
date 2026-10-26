"""Keep the SQLite database in a GitHub Release so a scheduled GitHub Actions
job and a hosted dashboard can share it (the file is too big for git itself).

    python -m earnsignal.remote download   # latest snapshot -> data/earnsignal.db
    python -m earnsignal.remote upload     # data/earnsignal.db -> release asset

Needs GITHUB_REPOSITORY ("owner/repo") and GITHUB_TOKEN / GH_TOKEN in the
environment. Both are set automatically inside GitHub Actions.
The release (tag `data-latest`) keeps two assets: the newest snapshot and the
previous one, so one bad run can always be rolled back.
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import requests

from . import config

API = "https://api.github.com"
TAG = "data-latest"
ASSET = "earnsignal.db.gz"
PREV = "earnsignal-prev.db.gz"
NEW = "earnsignal-new.db.gz"


def _repo_token(repo: str | None = None, token: str | None = None) -> tuple[str, str]:
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or os.environ.get("EARNSIGNAL_GITHUB_REPO")
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not repo or not token:
        raise SystemExit("Set GITHUB_REPOSITORY=owner/repo and GITHUB_TOKEN (automatic inside GitHub Actions).")
    return repo, token


def _h(token: str, **extra) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", **extra}


def get_release(repo: str, token: str) -> dict | None:
    r = requests.get(f"{API}/repos/{repo}/releases/tags/{TAG}", headers=_h(token), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def asset_info(repo: str, token: str, name: str = ASSET) -> dict | None:
    rel = get_release(repo, token)
    if not rel:
        return None
    return next((a for a in rel.get("assets", []) if a["name"] == name), None)


def download(dest: Path | None = None, repo: str | None = None, token: str | None = None, name: str = ASSET) -> Path | None:
    """Fetch the newest snapshot. Returns None (and leaves dest alone) when none exists yet."""
    repo, token = _repo_token(repo, token)
    dest = Path(dest) if dest else config.db_path()
    rel = get_release(repo, token)
    assets = {x["name"]: x for x in (rel or {}).get("assets", [])}
    a = assets.get(name) or (assets.get(PREV) if name == ASSET else None)
    if a is None:
        if rel and assets:
            raise RuntimeError(f"Release {TAG} exists but has no usable snapshot ({sorted(assets)}); refusing to start empty.")
        print(f"No snapshot in release {TAG} yet; starting from an empty database.")
        return None
    if a["name"] != name:
        print(f"'{name}' missing; falling back to {a['name']}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        gz = Path(td) / name
        with requests.get(a["url"], headers=_h(token, Accept="application/octet-stream"), stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(gz, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        tmp = dest.with_suffix(".db.part")
        with gzip.open(gz, "rb") as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out)
        check = sqlite3.connect(tmp)
        ok = check.execute("PRAGMA quick_check").fetchone()[0]
        check.close()
        if ok != "ok":
            raise RuntimeError(f"Downloaded snapshot failed integrity check: {ok}")
        for ext in ("-wal", "-shm"):
            Path(str(dest) + ext).unlink(missing_ok=True)
        tmp.replace(dest)
    print(f"Restored {dest} ({dest.stat().st_size / 1e6:.1f} MB) from {a['updated_at']}")
    return dest


def snapshot(src: Path, out_gz: Path) -> Path:
    """Consistent, compacted copy of a live SQLite DB (VACUUM INTO), gzipped."""
    with tempfile.TemporaryDirectory() as td:
        plain = Path(td) / "snap.db"
        con = sqlite3.connect(src)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute(f"VACUUM INTO '{plain}'")
        con.close()
        with open(plain, "rb") as f, gzip.open(out_gz, "wb", compresslevel=6) as g:
            shutil.copyfileobj(f, g)
    return out_gz


def upload(src: Path | None = None, repo: str | None = None, token: str | None = None) -> None:
    repo, token = _repo_token(repo, token)
    src = Path(src) if src else config.db_path()
    if not src.exists():
        raise SystemExit(f"No database at {src}")
    rel = get_release(repo, token)
    if rel is None:
        r = requests.post(f"{API}/repos/{repo}/releases", headers=_h(token), timeout=30, json={
            "tag_name": TAG, "name": "Database snapshot",
            "body": "Rolling SQLite snapshot written by the daily GitHub Actions job. Not a software release.",
            "prerelease": True, "make_latest": "false",
        })
        r.raise_for_status()
        rel = r.json()
    assets = {a["name"]: a for a in rel.get("assets", [])}
    with tempfile.TemporaryDirectory() as td:
        gz = snapshot(src, Path(td) / ASSET)
        size = gz.stat().st_size
        # 1) upload under a temporary name, so a failed upload never removes the current snapshot
        if NEW in assets:
            requests.delete(assets[NEW]["url"], headers=_h(token), timeout=30).raise_for_status()
        up = rel["upload_url"].split("{")[0]
        with open(gz, "rb") as f:
            r = requests.post(f"{up}?name={NEW}", headers=_h(token, **{"Content-Type": "application/gzip"}),
                              data=f, timeout=900)
        r.raise_for_status()
        new_url = r.json()["url"]
        # 2) rotate: current -> prev, new -> current
        if PREV in assets:
            requests.delete(assets[PREV]["url"], headers=_h(token), timeout=30).raise_for_status()
        if ASSET in assets:
            requests.patch(assets[ASSET]["url"], headers=_h(token), timeout=30, json={"name": PREV}).raise_for_status()
        requests.patch(new_url, headers=_h(token), timeout=30, json={"name": ASSET}).raise_for_status()
    print(f"Uploaded {ASSET} ({size / 1e6:.1f} MB) to {repo} release {TAG}")


def main(argv=None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in ("download", "upload"):
        raise SystemExit("usage: python -m earnsignal.remote download|upload")
    download() if argv[0] == "download" else upload()


if __name__ == "__main__":
    main()
