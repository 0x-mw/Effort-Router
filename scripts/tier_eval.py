#!/usr/bin/env python3
"""티어 판정 회귀 시험 — **실행하면 Claude 한도를 사용한다(사례 1건당 새 프로세스 1회)**.

사용:
  tier_eval.py --dry-run                      # 사례 파일 형식만 검증하고 표 출력(claude 호출 없음)
  tier_eval.py [--only T1,T4] [--jobs 3] [--config-dir ~/.claude-mw] [--workdir .] [--out tier_eval_out]

사례마다 `claude -p <요청+SUFFIX>`를 새 프로세스로 띄우고(도구는 Skill·Read 만), 최종 응답의
`판정 티어:` 첫 줄을 정확 비교로 채점한다. 문자열 포함 여부로 채점하지 않는다 — 응답 어딘가에
기대 티어 글자가 있기만 해도 통과하는 무의미한 통과를 막기 위해서다.
Skill·Read 밖의 도구를 쓴 사례는 UNSAFE_TOOL 로 실패 처리한다.
종료코드: 전부 통과 0, 하나라도 실패 1, 사례 파일 형식 오류 2.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUFFIX = " (시험이므로 티어 판정 출력까지만 하고, 실제 작업·파일 수정·외부 전송은 시작하지 마.)"
ALLOWED_TIERS = {"S", "M", "L", "XL", "보안감사", "D1", "D2", "D3"}
ALLOWED_TOOLS = {"Skill", "Read"}
REQUIRED_KEYS = ("id", "request", "expected_tiers", "expect_saving", "note")

TIER_RE = re.compile(r"판정 티어:\s*(XL|보안감사|D[123]|[SML])\b")
BLOCK_RE = re.compile(r"```text(.*?)```", re.S)
SAVING_RE = re.compile(r"절약 모드:\s*(ON|OFF)")


def parse_tier(text):
    """`판정 티어:` 첫 일치의 티어. 없으면 None."""
    m = TIER_RE.search(text or "")
    return m.group(1) if m else None


def parse_saving(text):
    """최초 ```text 블록 안의 `절약 모드:` 값(ON/OFF). 블록이나 줄이 없으면 None."""
    block = BLOCK_RE.search(text or "")
    if not block:
        return None
    m = SAVING_RE.search(block.group(1))
    return m.group(1) if m else None


def score(case, text, tools):
    """채점 결과 dict — ok 와 실패 사유(reason)를 담는다."""
    tier, saving = parse_tier(text), parse_saving(text)
    unsafe = sorted(set(tools) - ALLOWED_TOOLS)
    if unsafe:
        reason = "UNSAFE_TOOL:" + ",".join(unsafe)
    elif tier is None:
        reason = "NO_TIER"
    elif tier not in case["expected_tiers"]:
        reason = "WRONG_TIER"
    elif case.get("expect_saving") is True and saving != "ON":
        reason = "SAVING_NOT_ON"
    else:
        reason = ""
    return {"tier": tier, "saving": saving, "ok": not reason, "reason": reason}


def validate_cases(cases):
    """사례 목록 형식 오류 목록. 비어 있으면 정상."""
    if not isinstance(cases, list) or not cases:
        return ["사례 파일은 비어 있지 않은 목록이어야 함"]
    errors, seen = [], set()
    for n, c in enumerate(cases, 1):
        if not isinstance(c, dict):
            errors.append(f"{n}번째 항목이 객체가 아님")
            continue
        cid = c.get("id", f"#{n}")
        missing = [k for k in REQUIRED_KEYS if k not in c]
        if missing:
            errors.append(f"{cid}: 필수 키 없음 {missing}")
            continue
        if not isinstance(cid, str) or not cid:
            errors.append(f"{n}번째 항목: id 가 문자열이 아님")
        elif cid in seen:
            errors.append(f"{cid}: 중복 id")
        seen.add(cid if isinstance(cid, str) else n)
        if not isinstance(c["request"], str) or not c["request"].strip():
            errors.append(f"{cid}: request 가 비어 있음")
        tiers = c["expected_tiers"]
        if not isinstance(tiers, list) or not tiers:
            errors.append(f"{cid}: expected_tiers 는 비어 있지 않은 목록이어야 함")
        else:
            unknown = [t for t in tiers if t not in ALLOWED_TIERS]
            if unknown:
                errors.append(f"{cid}: 알 수 없는 티어 {unknown}")
        if c["expect_saving"] not in (None, True):
            errors.append(f"{cid}: expect_saving 은 null 또는 true 만 허용")
    return errors


def run_case(case, a, retry=True):
    """claude 를 새 프로세스로 한 번 띄우고 (사용 도구, 호출 스킬, 최종 텍스트, 종료코드)를 모은다."""
    cmd = ["claude", "-p", case["request"] + SUFFIX, "--output-format", "stream-json", "--verbose",
           "--tools", "Skill,Read", "--max-turns", "6"]
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(a.config_dir))
    try:
        # 셸을 거치지 않고 인자 목록으로 실행한다.
        p = subprocess.run(cmd, cwd=str(a.workdir), env=env,
                           capture_output=True, text=True, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        return [], [], "", "TIMEOUT"
    except OSError as e:
        if retry:  # 자동 업데이트가 실행 파일을 교체하는 동안의 짧은 공백일 수 있다
            time.sleep(float(os.environ.get("TIER_EVAL_RETRY_WAIT", "20")))
            return run_case(case, a, retry=False)
        return [], [], "", f"실행 실패({type(e).__name__})"
    tools, skills, final = [], [], ""
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "assistant":
            for c in ev.get("message", {}).get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tools.append(c.get("name", "?"))
                    if c.get("name") == "Skill":
                        skills.append((c.get("input") or {}).get("skill"))
        elif ev.get("type") == "result":
            final = ev.get("result", "") or ""
    return tools, skills, final, p.returncode


def print_cases(cases):
    print(f"{'사례':5} {'기대':10} {'절약':5} 요청")
    for c in cases:
        sav = "ON" if c.get("expect_saving") is True else "-"
        print(f"{c['id']:5} {'/'.join(c['expected_tiers']):10} {sav:5} {c['request']}")
    print(f"\n사례 {len(cases)}건")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config-dir", type=Path,
                    default=Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude-mw"))
    ap.add_argument("--workdir", type=Path, default=Path.cwd())
    ap.add_argument("--cases", type=Path, default=HERE / "tier_eval_cases.json")
    ap.add_argument("--only", default="", help="쉼표로 구분한 사례 id(예: T1,T4)")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=280)
    ap.add_argument("--out", type=Path, default=Path.cwd() / "tier_eval_out")
    ap.add_argument("--repeat", type=int, default=1, help="사례마다 N번 실행해 모두 통과해야 통과(일관성 확인, 한도 N배 사용)")
    ap.add_argument("--dry-run", action="store_true", help="사례 파일 검증과 표 출력만(claude 호출 없음)")
    a = ap.parse_args()

    try:
        cases = json.loads(a.cases.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"사례 파일을 읽지 못함: {a.cases} ({type(e).__name__})", file=sys.stderr)
        return 2
    errors = validate_cases(cases)
    if errors:
        for e in errors:
            print(f"형식 오류: {e}", file=sys.stderr)
        return 2
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        unknown = sorted(set(want) - {c["id"] for c in cases})
        if unknown:
            print(f"사례 파일에 없는 id: {unknown}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in want]

    if a.dry_run:
        print_cases(cases)
        return 0

    if a.repeat < 1:
        print("--repeat 는 1 이상이어야 함", file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    jobs = [(c, k) for c in cases for k in range(a.repeat)]
    with cf.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        runs = list(ex.map(lambda j: run_case(j[0], a), jobs))

    by_case = {}
    for (case, k), (tools, skills, final, rc) in zip(jobs, runs):
        name = case["id"] if a.repeat == 1 else f"{case['id']}_{k + 1}"
        (a.out / f"{name}.txt").write_text(final, encoding="utf-8")
        r = score(case, final, tools)
        if not isinstance(rc, int):  # 시간 초과·실행 실패
            r.update(ok=False, reason=rc)
        by_case.setdefault(case["id"], []).append((case, skills, tools, r, rc))

    summary = []
    for cid, items in by_case.items():
        case = items[0][0]
        tiers = [it[3]["tier"] for it in items]
        reasons = [it[3]["reason"] for it in items if it[3]["reason"]]
        summary.append({"id": cid, "request": case["request"],
                        "skills": sorted({str(x) for it in items for x in it[1]}),
                        "tools": sorted({t for it in items for t in it[2]}),
                        "tiers": tiers, "tier": tiers[0], "expected": case["expected_tiers"],
                        "expect_saving": case["expect_saving"], "saving": items[0][3]["saving"],
                        "runs": len(items), "passed_runs": sum(it[3]["ok"] for it in items),
                        "flaky": len(set(tiers)) > 1,
                        "ok": all(it[3]["ok"] for it in items),
                        "reason": "; ".join(sorted(set(reasons))), "exit": [it[4] for it in items]})
    (a.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                        encoding="utf-8")

    print(f"{'사례':5} {'호출된 스킬':24} {'판정(회차별)':14} {'기대':10} {'결과':4} {'절약':5} 사유")
    for s in summary:
        seen = ",".join(t or "-" for t in s["tiers"])
        flag = " ⚠흔들림" if s["flaky"] else ""
        print(f"{s['id']:5} {','.join(s['skills']) or '-':24} {seen:14} "
              f"{'/'.join(s['expected']):10} {'OK' if s['ok'] else 'XX':4} {s['saving'] or '-':5} "
              f"{s['reason']}{flag}")
    passed = sum(s["ok"] for s in summary)
    print(f"\n{passed}/{len(summary)} 통과 (사례당 {a.repeat}회) — 결과: {a.out}")
    return 0 if passed == len(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
