#!/usr/bin/env python3
"""세션 기록에서 티어 판정 분포(tiers)와 토큰 사용량 추정(usage)을 집계한다.

로컬 파일만 읽는다 — 모델 호출·네트워크 없음(한도를 쓰지 않는다). 출력은 건수·토큰 합계뿐이며
대화 내용은 출력하지 않는다.

사용:
  usage_report.py tiers [--days 7] [--include-tests] [--skill NAME ...] [--config-dir DIR ...]
  usage_report.py usage [--hours 5] [--days 7] [--include-tests] [--config-dir DIR ...]

--config-dir 기본값은 ~/.claude-mw. 회사 계정(~/.claude)도 보려면 직접 추가한다(여러 번 지정 가능).

한계: 토큰 합계는 **참고용 추정**이다. 실제 사용 한도가 모델별로 어떻게 가중·집계되는지,
5시간 창이 언제 시작되는지는 이 스크립트가 알 수 없다. 모델 사이의 상대 비교와 추세를 보는 용도다.
tiers 는 응답에 `[Effort Router]` 표지와 `판정 티어:` 줄이 함께 있는 메시지만 센다.
그래서 스킬을 불렀는데 블록을 빼먹은 응답은 판정 건수에 잡히지 않는다(과소집계). 이를 보완하려고
tiers 는 세션 기록의 `Skill` 도구 호출(스킬 이름별 호출 건수·세션 수)을 함께 세고, 호출은 있는데
판정 블록이 하나도 없는 세션 수를 보여 준다. 사용자가 슬래시 명령으로 직접 부른 경우가 `Skill`
도구 호출로 기록되는지는 확인하지 못했다 — 그런 호출은 빠질 수 있다.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TEST_MARKER = "(시험이므로"  # tier_eval.py 가 요청 뒤에 붙이는 문구 — 시험 세션 식별용
TIER_RE = re.compile(r"판정 티어:\s*(XL|보안감사|D[123]|[SML])\b")
SAVING_RE = re.compile(r"절약 모드:\s*(ON|OFF)")
DEFAULT_SKILLS = ("effort-router", "klic-effort-router")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _text_of(content):
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        if isinstance(c, dict) and c.get("type") == "text":
            out.append(c.get("text") or "")
    return "\n".join(out)


def _skills_of(content):
    """{tool_use id: 스킬 이름} — Skill 도구 호출만."""
    out = {}
    for c in content or []:
        if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Skill":
            name = (c.get("input") or {}).get("skill")
            if c.get("id") and isinstance(name, str):
                out[c["id"]] = name
    return out


def scan_file(path):
    """(시험 세션 여부, {메시지id: 기록}). 한 메시지가 여러 줄로 나뉘면 합친다."""
    is_test, msgs = False, {}
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return False, {}
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            m = d.get("message")
            if not isinstance(m, dict):
                continue
            if d.get("type") == "user":
                if TEST_MARKER in _text_of(m.get("content")):
                    is_test = True
                continue
            if d.get("type") != "assistant" or not m.get("id"):
                continue
            r = msgs.setdefault(m["id"], {"ts": None, "model": None, "usage": dict.fromkeys(USAGE_KEYS, 0),
                                          "text": "", "skills": {}})
            r["ts"] = r["ts"] or parse_ts(d.get("timestamp"))
            r["model"] = r["model"] or m.get("model")
            for k in USAGE_KEYS:  # 스트리밍 중간값이 있을 수 있어 최댓값을 쓴다
                v = (m.get("usage") or {}).get(k)
                if isinstance(v, int):
                    r["usage"][k] = max(r["usage"][k], v)
            r["text"] += _text_of(m.get("content")) + "\n"
            r["skills"].update(_skills_of(m.get("content")))
    return is_test, msgs


def collect(config_dirs, cutoff, include_tests):
    """메시지 id 기준으로 중복을 제거한 기록 목록. cutoff 보다 오래 수정된 파일은 건너뛴다."""
    seen, records, skipped_tests = {}, [], 0
    for cfg in config_dirs:
        proj = Path(cfg).expanduser() / "projects"
        if not proj.is_dir():
            continue
        for f in sorted(proj.rglob("*.jsonl")):
            try:
                if cutoff and datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < cutoff:
                    continue
            except OSError:
                continue
            is_test, msgs = scan_file(f)
            if is_test and not include_tests:
                skipped_tests += 1
                continue
            for mid, r in msgs.items():
                r["session"] = str(f)
                if mid in seen:  # 이어하기로 복제된 이전 메시지 — 한 번만 센다
                    prev = seen[mid]
                    prev["skills"].update(r["skills"])
                    for k in USAGE_KEYS:
                        prev["usage"][k] = max(prev["usage"][k], r["usage"][k])
                    continue
                seen[mid] = r
                records.append(r)
    return records, skipped_tests


def report_tiers(records, since):
    tiers, saving, by_day, n = defaultdict(int), defaultdict(int), defaultdict(lambda: defaultdict(int)), 0
    for r in records:
        if since and (r["ts"] is None or r["ts"] < since):
            continue
        t = TIER_RE.search(r["text"])
        if not t or "[Effort Router]" not in r["text"]:
            continue
        s = SAVING_RE.search(r["text"])
        n += 1
        tiers[t.group(1)] += 1
        if s:
            saving[s.group(1)] += 1
        if r["ts"]:
            by_day[r["ts"].astimezone().strftime("%m-%d")][t.group(1)] += 1
    return n, tiers, saving, by_day


def report_skills(records, since, names):
    """스킬 이름별 (호출 건수, 호출한 세션 수)와 '호출은 있는데 판정 블록이 없는 세션' 수."""
    calls = {n: set() for n in names}
    sessions = {n: set() for n in names}
    with_block = set()
    for r in records:
        if since and (r["ts"] is None or r["ts"] < since):
            continue
        for tid, name in r["skills"].items():
            if name in calls:
                calls[name].add(tid)
                sessions[name].add(r["session"])
        if TIER_RE.search(r["text"]) and "[Effort Router]" in r["text"]:
            with_block.add(r["session"])
    return {n: (len(calls[n]), len(sessions[n]), len(sessions[n] - with_block)) for n in names}


def report_usage(records, since):
    per_model = defaultdict(lambda: dict.fromkeys(USAGE_KEYS, 0) | {"messages": 0})
    for r in records:
        if since and (r["ts"] is None or r["ts"] < since):
            continue
        row = per_model[r["model"] or "(모델 불명)"]
        row["messages"] += 1
        for k in USAGE_KEYS:
            row[k] += r["usage"][k]
    return per_model


def fmt(n):
    return f"{n:,}"


def print_usage(title, per_model):
    print(f"\n[{title}]")
    if not per_model:
        print("  (해당 기간 기록 없음)")
        return
    print(f"  {'모델':26} {'메시지':>7} {'출력':>12} {'입력':>12} {'캐시쓰기':>12} {'캐시읽기':>14}")
    tot_out = sum(r["output_tokens"] for r in per_model.values()) or 1
    for model, r in sorted(per_model.items(), key=lambda kv: -kv[1]["output_tokens"]):
        print(f"  {model:26} {fmt(r['messages']):>7} {fmt(r['output_tokens']):>12} {fmt(r['input_tokens']):>12} "
              f"{fmt(r['cache_creation_input_tokens']):>12} {fmt(r['cache_read_input_tokens']):>14}"
              f"   출력 비중 {100 * r['output_tokens'] / tot_out:4.0f}%")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["tiers", "usage"])
    ap.add_argument("--config-dir", action="append", type=Path)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--hours", type=float, default=5)
    ap.add_argument("--skill", action="append", help="호출 건수를 셀 스킬 이름(여러 번 지정 가능, 기본 effort-router·klic-effort-router)")
    ap.add_argument("--include-tests", action="store_true", help="tier_eval 시험 세션도 포함")
    ap.add_argument("--now", help=argparse.SUPPRESS)  # 시험용: 기준 시각(ISO)
    a = ap.parse_args(argv)

    now = parse_ts(a.now) if a.now else datetime.now(timezone.utc)
    if now is None:
        print("--now 형식 오류", file=sys.stderr)
        return 2
    dirs = a.config_dir or [Path.home() / ".claude-mw"]
    span = timedelta(days=a.days) if a.mode == "tiers" else timedelta(days=max(a.days, a.hours / 24))
    records, skipped = collect(dirs, now - span - timedelta(days=1), a.include_tests)
    print(f"대상: {', '.join(str(d) for d in dirs)}  (시험 세션 {'포함' if a.include_tests else f'제외 {skipped}개'})")

    if a.mode == "tiers":
        n, tiers, saving, by_day = report_tiers(records, now - timedelta(days=a.days))
        print(f"\n[최근 {a.days}일 티어 판정] 총 {n}건")
        if not n:
            print("  (기록 없음)")
        for t in sorted(tiers, key=lambda x: (-tiers[x], x)):
            print(f"  {t:6} {tiers[t]:4}건  {100 * tiers[t] / n:4.0f}%")
        if saving:
            print(f"  절약 모드: ON {saving.get('ON', 0)}건 / OFF {saving.get('OFF', 0)}건")
        for day in sorted(by_day):
            print(f"  {day}  " + "  ".join(f"{t} {c}" for t, c in sorted(by_day[day].items())))
        names = tuple(a.skill) if a.skill else DEFAULT_SKILLS
        sk = report_skills(records, now - timedelta(days=a.days), names)
        print(f"\n[최근 {a.days}일 스킬 호출 — 세션 기록의 Skill 도구 호출]")
        for n in names:
            calls, sess, no_block = sk[n]
            extra = f", 이 중 판정 블록이 없는 세션 {no_block}개" if calls and no_block else ""
            print(f"  {n:22} {calls:4}건 (세션 {sess}개{extra})")
        if any(sk[n][2] for n in names):
            print("  → 호출은 있는데 판정 블록이 없는 세션이 있어, 위 판정 건수는 실제보다 적게 잡혔을 수 있다(과소집계).")
        print("\n주의: 응답에 판정 형식이 인용된 경우도 셀 수 있고, 블록을 빼먹은 응답은 못 센다 — 정확한 횟수가 아니라 분포를 보는 용도다.")
    else:
        print_usage(f"최근 {a.hours:g}시간 (참고용 추정)", report_usage(records, now - timedelta(hours=a.hours)))
        print_usage(f"최근 {a.days}일", report_usage(records, now - timedelta(days=a.days)))
        print("\n주의: 실제 한도 계기와 다를 수 있다 — 모델 간 상대 비교와 추세용이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
