#!/usr/bin/env python3
"""Check watched GitHub repos for new releases; write Chinese Markdown reports.

Uses only the GitHub API (GITHUB_TOKEN). Optional free GitHub Models
summarization with hard fallback to a Chinese template — AI failure never
fails the run.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("需要 PyYAML：pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
WATCHLIST = ROOT / "config" / "watchlist.yml"
STATE_PATH = ROOT / "state" / "last_seen.json"
REPORTS_DIR = ROOT / "reports"
README_PATH = ROOT / "README.md"
REPORTS_INDEX = REPORTS_DIR / "README.md"

API = "https://api.github.com"
MODELS_API = "https://models.github.ai/inference/chat/completions"
# Free GitHub Models model id (may change; failure falls back to template)
MODELS_MODEL = os.environ.get("GITHUB_MODELS_MODEL", "openai/gpt-4o-mini")


def github_token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def api_request(url: str, method: str = "GET", body: dict | None = None, accept: str = "application/vnd.github+json") -> Any:
    import time

    headers = {
        "Accept": accept,
        "User-Agent": "release-watcher",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    last_err: Exception | None = None
    for attempt in range(1, 5):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = RuntimeError(f"HTTP {e.code} for {url}: {err_body[:500]}")
            if e.code in (502, 503, 504, 429) and attempt < 4:
                time.sleep(2 * attempt)
                continue
            raise last_err from e
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < 4:
                time.sleep(2 * attempt)
                continue
            raise
    raise last_err or RuntimeError(f"request failed: {url}")


def load_watchlist() -> list[dict[str, str]]:
    with WATCHLIST.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    repos = data.get("repos") or []
    out = []
    for item in repos:
        if not item:
            continue
        owner = str(item.get("owner") or "").strip()
        name = str(item.get("name") or "").strip()
        if not owner or not name:
            print(f"跳过无效项: {item}", file=sys.stderr)
            continue
        out.append(
            {
                "owner": owner,
                "name": name,
                "note": str(item.get("note") or "").strip(),
                "key": f"{owner}/{name}",
            }
        )
    return out


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    with STATE_PATH.open(encoding="utf-8") as f:
        return json.load(f) or {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_releases(owner: str, name: str, per_page: int = 10) -> list[dict[str, Any]]:
    url = f"{API}/repos/{owner}/{name}/releases?per_page={per_page}"
    data = api_request(url)
    if not isinstance(data, list):
        return []
    # Skip drafts
    return [r for r in data if not r.get("draft")]


def extract_bullets(body: str, limit: int = 12) -> list[str]:
    if not body:
        return []
    bullets: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("- ", "* ", "+ ")):
            text = s[2:].strip()
        elif re.match(r"^\d+\.\s+", s):
            text = re.sub(r"^\d+\.\s+", "", s).strip()
        elif s.startswith("## ") or s.startswith("### "):
            text = s.lstrip("#").strip()
            text = f"【{text}】"
        else:
            continue
        # strip markdown noise lightly
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)
        text = text.strip()
        if text and text not in bullets:
            bullets.append(text)
        if len(bullets) >= limit:
            break
    if not bullets:
        # fallback: first non-empty paragraphs
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        for p in paras[:3]:
            one = " ".join(p.split())
            one = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", one)
            if len(one) > 200:
                one = one[:200] + "…"
            bullets.append(one)
    return bullets


def try_ai_summarize(project: str, tag: str, title: str, body: str) -> str | None:
    """Return Chinese summary markdown, or None on any failure."""
    token = github_token()
    if not token:
        return None
    if os.environ.get("DISABLE_GITHUB_MODELS", "").lower() in ("1", "true", "yes"):
        return None
    prompt = (
        "你是技术编辑。请把下面的 GitHub Release Notes 整理成简洁中文摘要。\n"
        "要求：\n"
        "1. 用 3～8 条要点，每条一行，以「- 」开头\n"
        "2. 保留版本号相关事实，不要编造\n"
        "3. 只输出要点列表，不要开场白和结尾\n\n"
        f"项目：{project}\n版本：{tag}\n标题：{title}\n\n"
        f"Release Notes：\n{(body or '')[:6000]}"
    )
    body_json = {
        "model": MODELS_MODEL,
        "messages": [
            {"role": "system", "content": "你用简体中文回答，只输出 Markdown 列表。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 800,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "release-watcher",
    }
    req = urllib.request.Request(
        MODELS_API,
        data=json.dumps(body_json).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        if not content:
            return None
        return content
    except Exception as e:  # noqa: BLE001 — intentional soft fail
        print(f"GitHub Models 不可用，改用模板摘要: {e}", file=sys.stderr)
        return None


def format_release_section(
    project: str,
    note: str,
    release: dict[str, Any],
    *,
    ai_used: bool,
) -> tuple[str, bool]:
    tag = release.get("tag_name") or ""
    title = release.get("name") or tag
    url = release.get("html_url") or ""
    published = release.get("published_at") or release.get("created_at") or ""
    prerelease = bool(release.get("prerelease"))
    body = release.get("body") or ""

    ai_text = try_ai_summarize(project, tag, title, body)
    used_ai = bool(ai_text)
    if ai_text:
        summary_block = ai_text
        method = "GitHub Models 中文摘要"
    else:
        bullets = extract_bullets(body)
        if bullets:
            summary_block = "\n".join(f"- {b}" for b in bullets)
        else:
            summary_block = "- （本版本未提供可读的 Release Notes 正文）"
        method = "模板摘要（原文要点提取）"

    lines = [
        f"## {project} `{tag}`",
        "",
        f"- **项目**：{project}" + (f"（{note}）" if note else ""),
        f"- **版本**：{tag}" + (" · 预发布" if prerelease else ""),
        f"- **标题**：{title}",
        f"- **发布时间**：{published}",
        f"- **链接**：{url}",
        f"- **摘要方式**：{method}",
        "",
        "### 更新要点",
        "",
        summary_block,
        "",
        "<details>",
        "<summary>原文 Release Notes（折叠）</summary>",
        "",
        body.strip() if body.strip() else "_（空）_",
        "",
        "</details>",
        "",
    ]
    return "\n".join(lines), used_ai


def shanghai_today() -> str:
    # Asia/Shanghai = UTC+8, no pytz needed for fixed offset
    from datetime import timedelta

    now = datetime.now(timezone.utc) + timedelta(hours=8)
    return now.strftime("%Y-%m-%d")


def update_reports_index(report_name: str, title: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        "# 更新报告历史\n\n"
        "这里保存每次检测到新版本后生成的中文摘要。\n\n"
        "- 文件名按日期：`YYYY-MM-DD.md`\n"
        "- 在 iPhone 的 GitHub App 里：打开本仓库 → **Code** → **reports** → 点某一天的文件即可阅读\n\n"
        "## 目录\n\n"
    )
    entry = f"- [{title}](./{report_name})\n"
    if not REPORTS_INDEX.exists():
        REPORTS_INDEX.write_text(header + entry, encoding="utf-8")
        return
    text = REPORTS_INDEX.read_text(encoding="utf-8")
    if report_name in text:
        return
    if "## 目录" in text:
        text = text.replace("## 目录\n\n", "## 目录\n\n" + entry, 1)
    else:
        text = text.rstrip() + "\n\n## 目录\n\n" + entry
    REPORTS_INDEX.write_text(text, encoding="utf-8")


def patch_root_readme(report_rel: str, summary_line: str) -> None:
    if not README_PATH.exists():
        return
    text = README_PATH.read_text(encoding="utf-8")
    marker_start = "<!-- LATEST_REPORT_START -->"
    marker_end = "<!-- LATEST_REPORT_END -->"
    block = (
        f"{marker_start}\n"
        f"**最新报告**：[{report_rel}]({report_rel})  \n"
        f"{summary_line}\n"
        f"{marker_end}"
    )
    if marker_start in text and marker_end in text:
        text = re.sub(
            re.escape(marker_start) + r".*?" + re.escape(marker_end),
            block,
            text,
            count=1,
            flags=re.S,
        )
    else:
        text = text.rstrip() + "\n\n## 最新报告\n\n" + block + "\n"
    README_PATH.write_text(text, encoding="utf-8")


def main() -> int:
    repos = load_watchlist()
    if not repos:
        print("watchlist 为空，请编辑 config/watchlist.yml", file=sys.stderr)
        return 1

    state = load_state()
    new_items: list[tuple[dict[str, str], dict[str, Any]]] = []
    ai_any = False

    for repo in repos:
        key = repo["key"]
        try:
            releases = fetch_releases(repo["owner"], repo["name"])
        except Exception as e:  # noqa: BLE001
            print(f"获取 {key} 失败: {e}", file=sys.stderr)
            continue
        if not releases:
            print(f"{key}: 没有 release，跳过")
            continue

        last = (state.get(key) or {}).get("tag")
        # Newest first from API
        if last is None:
            # First run: treat latest non-draft as new so we produce a real report
            chosen = [releases[0]]
            print(f"{key}: 首次运行，记录最新版本 {chosen[0].get('tag_name')}")
        else:
            chosen = []
            for r in releases:
                tag = r.get("tag_name")
                if tag == last:
                    break
                chosen.append(r)
            # chosen is newest-first; report oldest-first among new
            chosen = list(reversed(chosen))
            if chosen:
                print(f"{key}: 发现 {len(chosen)} 个新版本")
            else:
                print(f"{key}: 无新版本（当前 {last}）")

        for r in chosen:
            new_items.append((repo, r))

    # Always refresh state to latest known tag (even if no "new" for reporting)
    for repo in repos:
        key = repo["key"]
        try:
            releases = fetch_releases(repo["owner"], repo["name"], per_page=1)
        except Exception:
            continue
        if releases:
            r0 = releases[0]
            state[key] = {
                "tag": r0.get("tag_name"),
                "published_at": r0.get("published_at") or r0.get("created_at"),
                "html_url": r0.get("html_url"),
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

    if not new_items:
        save_state(state)
        print("没有需要写入报告的新版本。")
        # still save state on first-ish runs where we might have updated
        return 0

    day = shanghai_today()
    report_name = f"{day}.md"
    report_path = REPORTS_DIR / report_name
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    sections: list[str] = []
    for repo, release in new_items:
        section, used_ai = format_release_section(
            repo["key"], repo["note"], release, ai_used=False
        )
        ai_any = ai_any or used_ai
        sections.append(section)

    header = [
        f"# 版本更新摘要 · {day}",
        "",
        f"> 生成时间（上海时区日期）：{day}  ·  共 {len(new_items)} 条新版本",
        f"> 摘要方式：{'部分使用 GitHub Models' if ai_any else '全部使用零成本模板（原文要点提取）'}",
        "",
        "---",
        "",
    ]
    body = "\n---\n\n".join(sections)
    # If same-day report exists, append
    if report_path.exists():
        existing = report_path.read_text(encoding="utf-8")
        content = existing.rstrip() + "\n\n---\n\n" + body + "\n"
    else:
        content = "\n".join(header) + body + "\n"
    report_path.write_text(content, encoding="utf-8")

    update_reports_index(report_name, f"{day}（{len(new_items)} 条）")
    names = ", ".join(f"{r['key']} {rel.get('tag_name')}" for r, rel in new_items)
    patch_root_readme(f"reports/{report_name}", f"本次：{names}")

    save_state(state)
    print(f"已写入报告: {report_path}")
    print(f"AI_SUMMARIZATION={'yes' if ai_any else 'fallback'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
