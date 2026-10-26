"""Load settings and the company universe."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass
class Company:
    ticker: str
    name: str
    segment: str
    fiscal_year_end: int = 12
    ats_provider: str | None = None
    ats_token: str | None = None
    wikipedia: list[str] = field(default_factory=list)
    ios_app_id: int | None = None
    trends_terms: list[str] = field(default_factory=list)
    notes: str = ""
    tier: str = "core"


def _read_yaml(path: Path) -> Any:
    with open(path) as f:
        return yaml.safe_load(f)


def settings() -> dict:
    s = _read_yaml(CONFIG_DIR / "settings.yaml") or {}
    env_db = os.environ.get("EARNSIGNAL_DB")
    if env_db:
        s["db_path"] = env_db
    contact = os.environ.get("EARNSIGNAL_CONTACT") or s.get("contact") or ""
    s["contact"] = contact.strip()
    s.setdefault("rate_limits", {"default": 1})
    s.setdefault("history_start", "2016-01-01")
    s.setdefault("upcoming_window_days", 45)
    s.setdefault("sec_acceptance_tz", "America/New_York")
    s.setdefault("insider_start_year", 2018)
    return s


def db_path() -> Path:
    p = Path(settings()["db_path"])
    return p if p.is_absolute() else ROOT / p


def cache_dir() -> Path:
    p = Path(settings().get("cache_dir", "data/cache"))
    p = p if p.is_absolute() else ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def universe(path: Path | None = None) -> list[Company]:
    """Companies from every file in settings `universe_files` (core first).
    A ticker listed in more than one file keeps its first entry."""
    files = [path] if path else [CONFIG_DIR / f for f in settings().get("universe_files", ["universe.yaml"])]
    out, seen = [], set()
    for i, fp in enumerate(files):
        if not Path(fp).exists():
            continue
        for c in _parse_universe(Path(fp), tier="core" if i == 0 else "extended"):
            if c.ticker not in seen:
                seen.add(c.ticker)
                out.append(c)
    return out


def _parse_universe(path: Path, tier: str) -> list[Company]:
    raw = _read_yaml(path) or {}
    out = []
    for r in raw.get("companies", []):
        ats = r.get("ats") or {}
        out.append(
            Company(
                ticker=r["ticker"].upper(),
                name=r["name"],
                segment=r.get("segment", "other"),
                fiscal_year_end=int(r.get("fiscal_year_end", 12)),
                ats_provider=(ats.get("provider") or None),
                ats_token=(str(ats["token"]) if ats.get("token") else None),
                wikipedia=list(r.get("wikipedia") or []),
                ios_app_id=(int(r["ios_app_id"]) if r.get("ios_app_id") else None),
                trends_terms=list(r.get("trends_terms") or []),
                notes=r.get("notes", "") or "",
                tier=tier,
            )
        )
    return out


def benchmarks() -> list[str]:
    raw = _read_yaml(CONFIG_DIR / "universe.yaml")
    return list(raw.get("benchmarks", ["SPY"]))


def require_contact() -> str:
    c = settings()["contact"]
    if not c or "@" not in c:
        raise SystemExit(
            "SEC EDGAR and Wikimedia require a User-Agent with contact details.\n"
            "Set it once with:  export EARNSIGNAL_CONTACT=\"Your Name you@example.com\"\n"
            "or fill in `contact:` in config/settings.yaml."
        )
    return c
