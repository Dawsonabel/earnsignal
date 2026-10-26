"""The GitHub-release database sync, against an in-memory fake of the GitHub API."""
import json
import sqlite3

import pytest

from earnsignal import remote


class FakeResp:
    def __init__(self, status=200, js=None, content=b""):
        self.status_code, self._js, self.content = status, js, content

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, n):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGitHub:
    def __init__(self):
        self.release = None
        self.blobs = {}  # asset id -> bytes
        self.next_id = 1
        self.fail_upload = False

    def _asset(self, aid):
        return next(a for a in self.release["assets"] if a["id"] == aid)

    def get(self, url, headers=None, stream=False, timeout=None):
        if url.endswith(f"/releases/tags/{remote.TAG}"):
            return FakeResp(404) if self.release is None else FakeResp(200, json.loads(json.dumps(self.release)))
        aid = int(url.rsplit("/", 1)[-1])
        return FakeResp(200, content=self.blobs[aid])

    def post(self, url, headers=None, json=None, data=None, timeout=None):
        if url.endswith("/releases"):
            self.release = {"id": 1, "upload_url": "https://uploads.example/rel/1/assets{?name,label}", "assets": []}
            return FakeResp(201, self.release)
        if self.fail_upload:
            return FakeResp(500)
        name = url.split("name=")[-1]
        aid = self.next_id
        self.next_id += 1
        self.blobs[aid] = data.read()
        a = {"id": aid, "name": name, "url": f"https://api.example/assets/{aid}", "updated_at": f"2026-10-2{aid}T00:00:00Z"}
        self.release["assets"].append(a)
        return FakeResp(201, a)

    def patch(self, url, headers=None, json=None, timeout=None):
        self._asset(int(url.rsplit("/", 1)[-1]))["name"] = json["name"]
        return FakeResp(200)

    def delete(self, url, headers=None, timeout=None):
        aid = int(url.rsplit("/", 1)[-1])
        self.release["assets"] = [a for a in self.release["assets"] if a["id"] != aid]
        return FakeResp(204)


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    for m in ("get", "post", "patch", "delete"):
        monkeypatch.setattr(remote.requests, m, getattr(fake, m))
    return fake


def _db(path, value):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS t (v INTEGER)")
    con.execute("DELETE FROM t")
    con.execute("INSERT INTO t VALUES (?)", (value,))
    con.commit()
    con.close()


def _val(path):
    con = sqlite3.connect(path)
    v = con.execute("SELECT v FROM t").fetchone()[0]
    con.close()
    return v


def test_first_run_starts_empty(gh, tmp_path):
    assert remote.download(tmp_path / "x.db", repo="o/r", token="t") is None


def test_upload_download_and_rotation(gh, tmp_path):
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _db(src, 1)
    remote.upload(src, repo="o/r", token="t")
    _db(src, 2)
    remote.upload(src, repo="o/r", token="t")
    names = sorted(a["name"] for a in gh.release["assets"])
    assert names == [remote.PREV, remote.ASSET]
    remote.download(dst, repo="o/r", token="t")
    assert _val(dst) == 2
    remote.download(dst, repo="o/r", token="t", name=remote.PREV)
    assert _val(dst) == 1


def test_failed_upload_keeps_current_snapshot(gh, tmp_path):
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _db(src, 1)
    remote.upload(src, repo="o/r", token="t")
    gh.fail_upload = True
    _db(src, 99)
    with pytest.raises(RuntimeError):
        remote.upload(src, repo="o/r", token="t")
    remote.download(dst, repo="o/r", token="t")
    assert _val(dst) == 1


def test_refuses_to_start_empty_when_release_has_only_junk(gh, tmp_path):
    gh.release = {"id": 1, "upload_url": "u{", "assets": [{"id": 9, "name": "other.bin", "url": "https://api.example/assets/9"}]}
    with pytest.raises(RuntimeError):
        remote.download(tmp_path / "x.db", repo="o/r", token="t")
