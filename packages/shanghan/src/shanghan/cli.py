"""Shanghan-Hermes CLI — 供 Claude Code / Codex / OpenClaw 以子进程方式调用。

用法:
  python -m shanghan.cli stats
  python -m shanghan.cli ask "恶寒发热无汗脉浮紧应该匹配什么方证?" --role doctor
  python -m shanghan.cli ask "我最近怕冷发烧,求科普" --role patient
  python -m shanghan.cli paper
"""
from __future__ import annotations

import argparse
import json

from shanghan.pipeline import ShanghanPipeline
from shanghan.skill_rag import SkillRAG


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shanghan")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    sub.add_parser("paper")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--role", default="doctor",
                     choices=["doctor", "researcher", "patient"])
    args = parser.parse_args(argv)

    result = ShanghanPipeline().run()
    if args.cmd == "stats":
        print(json.dumps(result.stats, ensure_ascii=False, indent=2))
        return 0
    rag = SkillRAG(result)
    if args.cmd == "paper":
        print(json.dumps(rag.ask("生成论文草稿", role="researcher"),
                         ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(rag.ask(args.question, role=args.role),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
