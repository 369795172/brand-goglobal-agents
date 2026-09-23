#!/usr/bin/env python3
"""GoGlobal Content Crew — minimal runnable core loop.

One topic in, five platform-native content drafts out, brand-voice
constrained at the prompt layer, with a publish-gate checklist attached.

Zero third-party dependencies (stdlib only). Two modes:

  --dry-run   print the full orchestration (prompts per platform); no key needed
  live mode   call any OpenAI-compatible endpoint (OPENAI_BASE_URL / OPENAI_API_KEY / GGC_MODEL)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent

# ---------------------------------------------------------------- brand voice

VOICE_CONSTRAINTS = {
    "sentence_rhythm": "核心判断 6-15 字单独成句；解释 15-25 字一句一论点；超 25 字拆句",
    "stance": "高置信用「必须/不可行」；不确定标「我认为/推断」；事实、推断、猜测三档分开",
    "evidence": "数字保留源头精度；关键判断挂来源或数据锚点",
    "metaphor_domain": "系统/架构类比优先（闭环、管道、水位）；禁文学比喻（火焰、旅程、绽放）",
    "banned_words": ["赋能", "抓手", "打法", "拉齐", "让我们一起", "干货满满",
                     "game-changer", "unlock", "unleash", "delve into"],
    "opening": "第 2-3 句进正题；禁「在当今快速变化的时代」式开场",
    "closing": "抛具体问题或行动选项收尾；禁全文复述总结与升华",
}

PLATFORM_SPECS = {
    "x": {
        "file": "x_thread.md",
        "lang": "English",
        "form": "thread of 2-4 short posts; one assertion per post; data or contrarian hook first; cite sources",
    },
    "reddit": {
        "file": "reddit_post.md",
        "lang": "English",
        "form": "long-context post, 250-450 words; real data and personal build log style; zero marketing tone; end with a concrete question",
    },
    "xiaohongshu": {
        "file": "xiaohongshu_note.md",
        "lang": "简体中文",
        "form": "第一人称体：「我实际做了 X，结果是 Y」；正文 3-5 段；标注实践边界；标题带具体数字",
    },
    "instagram": {
        "file": "instagram_caption.md",
        "lang": "English",
        "form": "caption for a visual-first post; 2-3 sentences; concrete scene, no hashtag spam (max 5 tags)",
    },
    "discord": {
        "file": "discord_announcement.md",
        "lang": "English",
        "form": "community announcement in a builder/DIY server; plain tone; what we shipped, what is next, one CTA",
    },
}

PUBLISH_GATE = [
    "标题含具体数字或具体场景（非泛化口号）",
    "标签/社区规则核对（ subreddit / 版块规范）",
    "来源锚点：文中数字均有出处或标注推断",
    "发布时机核对（目标时区工作时段）",
    "声纹自检：禁用词表零命中",
]

DEFAULT_TOPIC = "为什么你的 AI 助手记不住上下文：三层上下文的工程解法"

# ---------------------------------------------------------------- helpers


def build_prompt(topic: str, spec: dict) -> list[dict]:
    v = VOICE_CONSTRAINTS
    system = (
        "You are a senior content writer inside a brand content pipeline. "
        "Hard constraints (violations are rejected by the editor agent):\n"
        f"- Rhythm: {v['sentence_rhythm']}\n"
        f"- Stance: {v['stance']}\n"
        f"- Evidence: {v['evidence']}\n"
        f"- Metaphors: {v['metaphor_domain']}\n"
        f"- Opening: {v['opening']}\n"
        f"- Closing: {v['closing']}\n"
        f"- Banned words (never use): {', '.join(v['banned_words'])}\n"
        "Output only the finished draft, no meta commentary."
    )
    user = (
        f"Brand context: an indie builder brand documenting real AI-engineering "
        f"practice; audience = AI builders moving from tool use to durable systems.\n"
        f"Platform: {spec['form']} (language: {spec['lang']})\n"
        f"Topic: {topic}\n"
        "Write the platform-native draft now."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_llm(messages: list[dict], base_url: str, api_key: str, model: str) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = json.dumps({"model": model, "messages": messages, "temperature": 0.7}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM endpoint returned HTTP {e.code}: {e.read().decode()[:200]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach LLM endpoint {url}: {e.reason}") from e
    try:
        return body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"unexpected LLM response shape: {json.dumps(body)[:200]}") from e


def main() -> int:
    ap = argparse.ArgumentParser(description="GoGlobal Content Crew minimal core loop")
    ap.add_argument("--topic", default=DEFAULT_TOPIC, help="content topic")
    ap.add_argument("--dry-run", action="store_true", help="print orchestration only, no API call")
    ap.add_argument("--platforms", default="all", help="comma list or 'all'")
    args = ap.parse_args()

    names = list(PLATFORM_SPECS) if args.platforms == "all" else [p.strip() for p in args.platforms.split(",")]
    bad = [n for n in names if n not in PLATFORM_SPECS]
    if bad:
        print(f"unknown platform(s): {bad}; valid: {list(PLATFORM_SPECS)}", file=sys.stderr)
        return 2

    base_url = os.environ.get("OPENAI_BASE_URL", "")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("GGC_MODEL", "glm-4.7")
    live = not args.dry_run
    if live and not (base_url and api_key):
        print("live mode needs OPENAI_BASE_URL and OPENAI_API_KEY (or use --dry-run)", file=sys.stderr)
        return 2

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = HERE / "out" / ts
    if live:
        out_dir.mkdir(parents=True, exist_ok=True)

    package = {
        "topic": args.topic,
        "mode": "dry-run" if args.dry_run else "live",
        "model": model if live else None,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "platforms": {},
        "publish_gate": [{"item": g, "status": "PENDING-HUMAN"} for g in PUBLISH_GATE],
    }

    for name in names:
        spec = PLATFORM_SPECS[name]
        messages = build_prompt(args.topic, spec)
        print(f"\n=== [{name}] -> {spec['file']} ({spec['lang']}) ===")
        if args.dry_run:
            for m in messages:
                print(f"--- {m['role']} ---\n{m['content']}\n")
            package["platforms"][name] = {"file": spec["file"], "status": "dry-run (prompt only)"}
        else:
            draft = call_llm(messages, base_url, api_key, model)
            (out_dir / spec["file"]).write_text(draft, encoding="utf-8")
            print(draft[:600] + ("\n...[truncated]" if len(draft) > 600 else ""))
            package["platforms"][name] = {"file": spec["file"], "chars": len(draft), "status": "draft"}

    if live:
        pkg_path = out_dir / "package.json"
        pkg_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\npackage written: {pkg_path}")
    print("\npublish gate (human confirmation required before posting):")
    for g in PUBLISH_GATE:
        print(f"  [ ] {g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
