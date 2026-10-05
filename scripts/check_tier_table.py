#!/usr/bin/env python3
"""reference/klic_skill_tiers.md 의 대응표가 설치된 스킬과 어긋나지 않는지 점검한다(오프라인).

점검 두 가지
  1. 표에 적힌 스킬(예: `(klic-quote)`)의 SKILL.md 가 스킬 폴더에 있는가 — 없으면 오류(종료코드 1)
  2. 각 게이트 항목의 핵심어가 그 스킬 SKILL.md 에 하나라도 있는가 — 없으면 경고(--strict 면 오류)

한계: 핵심어가 본문에 **있다**는 것만 본다. 게이트의 의미·범위가 같은지는 확인하지 못한다.
스킬을 고치거나 표를 고칠 때 이름이 어긋났는지 빨리 잡는 용도다.

사용: check_tier_table.py [--table FILE] [--skills-dir DIR] [--strict]
"""
import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STOP = {"확인", "검증", "점검", "계산", "생성", "대조", "결과", "기준", "자동", "세부", "항목", "시", "및", "전체"}


def parse_rows(text):
    """표 본문 행에서 (업무, [스킬...], [게이트 항목...])을 뽑는다."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= set("-: "):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 4 or cols[0] == "업무(스킬)":
            continue
        skills = []
        # 스킬은 칸 끝의 괄호에만 적는다 — 본문 중간의 `(xlsx)` 같은 파일 형식 표기는 스킬이 아니다
        m = re.search(r"\(([a-z][a-z0-9-]*(?:\s*,\s*[a-z][a-z0-9-]*)*)\)\s*$", cols[0])
        if m:
            skills += [s.strip() for s in m.group(1).split(",")]
        gates = [g.strip() for g in re.split(r"[,、]\s*(?![^()]*\))", cols[2]) if g.strip()]
        rows.append((cols[0], skills, gates))
    return rows


def tokens(gate):
    gate = re.sub(r"`[^`]*`", " ", gate)  # 코드 조각·명령은 제외
    toks = re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]{1,}", gate)
    return [t for t in toks if t not in STOP]


def check(table_text, skills_dir):
    errors, warnings = [], []
    for task, skills, gates in parse_rows(table_text):
        if not skills:
            warnings.append(f"[{task}] 표에서 스킬 이름을 찾지 못함")
            continue
        body = ""
        for s in skills:
            f = skills_dir / s / "SKILL.md"
            if not f.is_file():
                errors.append(f"[{task}] 스킬 `{s}` 의 SKILL.md 없음: {f}")
            else:
                body += f.read_text(encoding="utf-8", errors="replace")
        if not body:
            continue
        for g in gates:
            toks = tokens(g)
            if toks and not any(t in body for t in toks):
                warnings.append(f"[{task}] 게이트 '{g}' 의 핵심어({', '.join(toks[:3])})가 스킬 본문에 없음")
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--table", type=Path, default=HERE.parent / "reference" / "klic_skill_tiers.md")
    ap.add_argument("--skills-dir", type=Path, default=Path.home() / ".claude-mw" / "skills")
    ap.add_argument("--strict", action="store_true", help="경고도 오류로 취급")
    a = ap.parse_args(argv)
    try:
        text = a.table.read_text(encoding="utf-8")
    except OSError as e:
        print(f"표 파일을 읽지 못함: {a.table} ({type(e).__name__})", file=sys.stderr)
        return 2
    rows = parse_rows(text)
    if not rows:
        print("표에서 행을 찾지 못함 — 형식이 바뀌었는지 확인", file=sys.stderr)
        return 2
    errors, warnings = check(text, a.skills_dir)
    for e in errors:
        print("오류:", e)
    for w in warnings:
        print("경고:", w)
    print(f"\n행 {len(rows)}개 점검 — 오류 {len(errors)}건, 경고 {len(warnings)}건 (핵심어 존재만 확인, 의미 일치는 보장 못 함)")
    return 1 if errors or (a.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
