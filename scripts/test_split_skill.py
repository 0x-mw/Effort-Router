#!/usr/bin/env python3
"""split_skill.py 오프라인 시험 — 합성 입력만 사용(저장소·네트워크 불필요)."""
import io
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import split_skill as ss  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else f"  → {detail}"))


SRC = """---
name: effort-router
description: 시험용
---

# Effort Router

머리말

## 설치·업데이트 요청 계약

설치 본문 줄1
설치 본문 줄2

## When to Use

- 유지

## 1. 티어 판정

티어 본문 (§2 참조)

### 하위 절

하위 본문

## 2. 라우팅 테이블

| 표 | 값 |
|---|---|
| a | b |

## ●●● 팬아웃

팬아웃

## 3. 실행 메커니즘 (제약)

제약

## 4. 리뷰 판정과 merge 권한

리뷰

## 5. 상태·인계 계약 (M티어 이상)

상태

### 단계 상태 = state.json

json 설명

## 6. 환경 매핑

매핑

## 가이드 파일 = 실패 원장

원장

## Output Contract

계약

## 자주 하는 실패

실패

## 프로젝트 티어 ≠ 작업 티어

유지2
"""


def run(args, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdin
    try:
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        with redirect_stdout(out), redirect_stderr(err):
            rc = ss.main(args)
    finally:
        sys.stdin = old
    return rc, out.getvalue(), err.getvalue()


with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    src = t / "SKILL.md"; src.write_text(SRC, encoding="utf-8")
    ref = t / "ref"
    rc, out, err = run([str(src), "--out", str(ref)])
    check("정상 입력은 종료코드 0·세 파일 생성", rc == 0 and sorted(p.name for p in ref.iterdir()) ==
          ["code_tiers.md", "harness_install.md", "state_handoff.md"], out + err)

    code = (ref / "code_tiers.md").read_text(encoding="utf-8")
    state = (ref / "state_handoff.md").read_text(encoding="utf-8")
    inst = (ref / "harness_install.md").read_text(encoding="utf-8")
    check("절이 맞는 파일로 분류됨",
          "티어 본문" in code and "리뷰" in code and "실패" in code and "상태" in state
          and "설치 본문 줄1" in inst and "매핑" in inst and "원장" in inst, "")
    check("하위 절(###)이 부모와 함께 이동", "하위 본문" in code and "json 설명" in state, "")
    check("표·줄 구성이 원문 그대로(줄 단위 보존)",
          "| a | b |" in code and "설치 본문 줄1\n설치 본문 줄2" in inst, "")
    check("KEEP 절(When to Use·Output Contract·프로젝트 티어)은 출력에 없음",
          all(x not in code + state + inst for x in ("- 유지", "\n계약\n", "유지2")), "")
    check("원문 줄이 하나도 사라지지 않음(KEEP·머리말 제외)",
          all(line in (code + state + inst) for line in SRC.split("\n")
              if line.strip() and not line.startswith(("---", "name:", "description:", "# Effort", "머리말", "- 유지", "계약", "유지2",
                                                        "## When to Use", "## Output Contract", "## 프로젝트 티어"))), "")
    check("머리에 원문 복사본·우선순위 안내 포함", "원문 그대로 옮긴 것" in code and "핵심본이 우선" in code, code[:200])

    rc, out, err = run([str(src), "--out", str(ref), "--check"])
    check("--check: 방금 생성한 것과 일치", rc == 0 and "모두 일치" in out, out + err)
    (ref / "state_handoff.md").write_text(state + "손으로 고침\n", encoding="utf-8")
    rc, out, err = run([str(src), "--out", str(ref), "--check"])
    check("--check: 손으로 고치면 불일치(종료코드 1)", rc == 1 and "state_handoff.md" in out, out + err)

    rc, out, err = run(["-", "--out", str(t / "ref2")], stdin=SRC)
    check("표준입력(-)으로도 동작", rc == 0 and (t / "ref2" / "code_tiers.md").is_file(), out + err)

    rc, out, err = run(["-", "--out", str(t / "ref3")], stdin=SRC + "\n## 새로 생긴 절\n\n내용\n")
    check("새 절이 생기면 오류로 멈춤(파일 미생성)", rc == 2 and "새로 생긴 절" in err and not (t / "ref3").exists(), out + err)

    rc, out, err = run(["-", "--out", str(t / "ref4")], stdin=SRC.replace("## 6. 환경 매핑", "## 6. 이름 바뀜"))
    check("기대한 절이 입력에서 사라져도 오류(조용한 누락 방지)", rc == 2, out + err)

    rc, out, err = run([str(t / "없는파일.md"), "--out", str(t / "ref5")])
    check("입력 파일이 없으면 종료코드 2", rc == 2, out + err)

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
