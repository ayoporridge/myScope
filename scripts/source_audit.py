#!/usr/bin/env python3
"""
source_audit.py
生成哈勃半径订阅源边界治理报告。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

try:
    from _metrics import record_last_run, record_metrics
except ImportError:  # pragma: no cover - package import path for tests
    from scripts._metrics import record_last_run, record_metrics


PROJECT_DIR = Path(__file__).parent.parent
SUBSCRIPTIONS_PATH = PROJECT_DIR / "subscriptions.yaml"
TIERS_PATH = PROJECT_DIR / "source_tiers.yaml"
REPORT_DIR = PROJECT_DIR / "reports"


def load_subscription_sources(path: Path = SUBSCRIPTIONS_PATH) -> list[dict]:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = []
    for account in config.get("wechat", {}).get("accounts", []) or []:
        sources.append({"kind": "wechat", "name": str(account), "id": f"wechat:{account}"})
    for podcast in config.get("xiaoyuzhou", {}).get("podcasts", []) or []:
        name = podcast.get("title") if isinstance(podcast, dict) else podcast
        sources.append({"kind": "podcast", "name": str(name), "id": f"podcast:{name}"})
    for feed in config.get("blogs", {}).get("feeds", []) or []:
        name = feed.get("title") if isinstance(feed, dict) else feed
        sources.append({"kind": "blog", "name": str(name), "id": f"blog:{name}"})
    return sources


def load_tiers(path: Path = TIERS_PATH) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tiers = {}
    for tier, values in (config.get("tiers", {}) or {}).items():
        tiers[tier] = {str(v) for v in (values or [])}
    return tiers


def classify_sources(sources: list[dict], tiers: dict[str, set[str]]) -> dict:
    by_kind = Counter(s["kind"] for s in sources)
    by_tier = defaultdict(list)
    for source in sources:
        assigned = "uncategorized"
        for tier, values in tiers.items():
            if source["id"] in values or source["name"] in values:
                assigned = tier
                break
        by_tier[assigned].append(source)
    return {
        "total": len(sources),
        "by_kind": dict(by_kind),
        "by_tier": {tier: len(items) for tier, items in sorted(by_tier.items())},
        "uncategorized": by_tier.get("uncategorized", []),
    }


def render_report(summary: dict) -> str:
    lines = [
        "# MyScope Source Audit",
        "",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 总览",
        "",
        f"- 总源数：{summary['total']}",
    ]
    for kind, count in sorted(summary["by_kind"].items()):
        lines.append(f"- {kind}: {count}")
    lines.extend(["", "## 分层", ""])
    for tier, count in sorted(summary["by_tier"].items()):
        lines.append(f"- {tier}: {count}")
    lines.extend(["", "## 未分层源（前 80 个）", ""])
    for source in summary["uncategorized"][:80]:
        lines.append(f"- `{source['id']}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate source boundary audit report")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    sources = load_subscription_sources()
    summary = classify_sources(sources, load_tiers())
    report = render_report(summary)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else REPORT_DIR / f"source_audit_{datetime.now().strftime('%Y-%m-%d')}.md"
    output.write_text(report, encoding="utf-8")

    record_last_run("source_audit")
    record_metrics(
        "source_audit",
        sources_total=summary["total"],
        uncategorized=len(summary["uncategorized"]),
    )
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
