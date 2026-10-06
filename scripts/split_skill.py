#!/usr/bin/env python3
"""업스트림 형태의 SKILL.md 본문에서 긴 절을 reference/ 의 세 파일로 **원문 그대로** 옮겨 만든다.

왜: SKILL.md 는 호출할 때마다 통째로 문맥에 들어간다(57KB ≈ 약 25,000토큰). 코드 L·XL, 상태 인계,
설치 같은 특정 상황에서만 필요한 절은 reference/ 로 빼고, 필요할 때만 읽게 한다.

사용:
  split_skill.py <SKILL.md 경로 | -> --out <reference 폴더> [--check]
  예) git show remove-jev:SKILL.md | python3 scripts/split_skill.py - --out reference

- 입력은 우리 overlay 가 없는 업스트림 본문(remove-jev 브랜치의 SKILL.md)이다.
- 옮기는 절은 아래 BUCKETS 에 적힌 제목과 하위 절이다. 글자를 바꾸지 않는다(머리 안내문만 추가).
- BUCKETS 에도 KEEP 에도 없는 새 `## ` 절이 나오면 오류로 멈춘다 — 어느 파일로 보낼지 사람이 정한다.
- KEEP 절은 SKILL.md 핵심본이 직접 담는다(이 스크립트는 만들지 않는다).
- --check: 기존 출력이 입력과 일치하는지만 확인(손으로 고쳐졌거나 입력이 바뀌면 종료코드 1).
"""
import argparse
import re
import sys
from pathlib import Path

# 출력 파일 → (제목 접두어 목록, 머리 설명)
BUCKETS = {
    "code_tiers.md": (
        ["## 1. 티어 판정", "## 2. 라우팅 테이블", "## ●●● 팬아웃", "## 3. 실행 메커니즘",
         "## 4. 리뷰 판정과 merge 권한", "## 자주 하는 실패"],
        "코드 작업 M·L·XL·보안감사와 산출 문서·게이트 변경을 다룰 때 읽는다 — 티어 판정 전문, 라우팅 표(Codex 포함), 팬아웃, 실행 제약, 리뷰 판정, 자주 하는 실패.",
    ),
    "state_handoff.md": (
        ["## 5. 상태·인계 계약"],
        "단계 사이에 인계·진행 상태를 운반할 때(M티어 이상 코드 작업의 계획→구현→리뷰, 긴급 트랙 사후 절차) 읽는다.",
    ),
    "harness_install.md": (
        ["## 설치·업데이트 요청 계약", "## 가이드 파일 = 실패 원장", "## 6. 환경 매핑"],
        "Codex·ChatGPT·Gemini 등 다른 하니스에 설치하거나 대응 관계를 확인할 때, 실패 원장 규칙을 다룰 때 읽는다.",
    ),
}
KEEP = ["# Effort Router", "## When to Use", "## 프로젝트 티어 ≠ 작업 티어", "## Output Contract"]

NOTE = (
    "> 이 파일은 업스트림 SKILL.md 본문을 **원문 그대로 옮긴 것**이다(`scripts/split_skill.py` 가 생성 — 손으로 고치지 않는다).\n"
    "> 본문의 `§N` 표기는 reference/ 안의 같은 번호 절을 가리킨다. 이 파일과 SKILL.md 핵심본이 충돌하면 **핵심본이 우선**한다 "
    "(예: 절약 모드의 model override 예외, 문서 티어 D1~D3, 외부 모델 게이트)."
)


def split_sections(text):
    """[(제목줄, 본문 전체)] — 최상위 `## ` 기준. 첫 항목은 머리말(# 제목 포함)."""
    parts, cur_title, cur = [], None, []
    for line in text.split("\n"):
        if re.match(r"^## ", line) or (cur_title is None and line.startswith("# ")):
            if cur_title is not None or cur:
                parts.append((cur_title, "\n".join(cur)))
            cur_title, cur = line, [line]
        else:
            cur.append(line)
    parts.append((cur_title, "\n".join(cur)))
    return parts


def classify(title):
    for fname, (prefixes, _) in BUCKETS.items():
        if any(title.startswith(p) for p in prefixes):
            return fname
    if any(title.startswith(k) for k in KEEP):
        return None
    raise ValueError(title)


def build(text):
    sections = [(t, b) for t, b in split_sections(text) if t]
    out = {f: [] for f in BUCKETS}
    unknown = []
    for title, body in sections:
        try:
            f = classify(title)
        except ValueError:
            unknown.append(title)
            continue
        if f:
            out[f].append((title, body.rstrip("\n")))
    if unknown:
        raise ValueError("새 절이라 분류할 수 없음 — BUCKETS 또는 KEEP 에 추가해야 한다: " + "; ".join(unknown))
    files = {}
    for fname, (prefixes, desc) in BUCKETS.items():
        titles = [t for t, _ in out[fname]]
        missing = [p for p in prefixes if not any(t.startswith(p) for t in titles)]
        if missing:
            raise ValueError(f"{fname}: 입력에 없는 절 — {missing}")
        head = f"# reference — {fname[:-3]}\n\n{desc}\n\n{NOTE}\n\n"
        files[fname] = head + "\n\n".join(b for _, b in out[fname]) + "\n"
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="SKILL.md 경로, 표준입력이면 -")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--check", action="store_true", help="기존 출력과 일치하는지만 확인")
    a = ap.parse_args(argv)
    try:
        text = sys.stdin.read() if a.input == "-" else Path(a.input).read_text(encoding="utf-8")
    except OSError as e:
        print(f"입력을 읽지 못함: {a.input} ({type(e).__name__})", file=sys.stderr)
        return 2
    try:
        files = build(text)
    except ValueError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2
    if a.check:
        bad = [f for f, c in files.items()
               if not (a.out / f).is_file() or (a.out / f).read_text(encoding="utf-8") != c]
        for f in bad:
            print(f"불일치: {a.out / f}")
        print("모두 일치" if not bad else f"{len(bad)}개 불일치 — split_skill.py 를 다시 실행")
        return 1 if bad else 0
    a.out.mkdir(parents=True, exist_ok=True)
    for f, c in files.items():
        (a.out / f).write_text(c, encoding="utf-8")
        print(f"{a.out / f}  {len(c.encode()):,} 바이트")
    return 0


if __name__ == "__main__":
    sys.exit(main())
