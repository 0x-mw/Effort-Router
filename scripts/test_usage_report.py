#!/usr/bin/env python3
"""usage_report.py 오프라인 시험 — 합성 세션 기록만 사용(실제 기록·모델 호출 없음)."""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import usage_report as ur  # noqa: E402

results = []
NOW = "2026-10-06T12:00:00Z"
SECRET = "비밀계약금액-987654321원"  # 출력에 절대 나오면 안 되는 문자열


def check(name, cond, detail=""):
    results.append(cond)
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else f"  → {detail}"))


def asst(mid, ts, model, text, out=100, inp=10, cw=0, cr=0):
    return {"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "model": model, "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": inp, "output_tokens": out,
                  "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}}


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def contract(tier, saving="OFF", extra=""):
    return f"```text\n[Effort Router]\n- 판정 티어: {tier}\n- 절약 모드: {saving}\n```\n{extra}"


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ur.main(argv)
    return rc, buf.getvalue()


with tempfile.TemporaryDirectory() as t:
    cfg = Path(t) / "cfg"
    p = cfg / "projects" / "p1"
    # 일반 세션: D2 1건(절약 OFF), D3 1건(절약 ON), 판정 형식이 없는 메시지, 비밀 문자열이 든 메시지
    write(p / "s1.jsonl", [
        user("견적서 만들어줘"),
        asst("m1", "2026-10-06T10:00:00Z", "claude-sonnet-5-5", contract("D2"), out=500),
        asst("m2", "2026-10-06T10:30:00Z", "claude-opus-5-5", contract("D3", "ON"), out=2000),
        asst("m3", "2026-10-06T11:00:00Z", "claude-opus-5-5", f"일반 답변 {SECRET}", out=300),
        asst("m4", "2026-10-01T09:00:00Z", "claude-opus-5-5", contract("D1"), out=700),  # 5일 전(기간 안, 5시간 밖)
        asst("m5", "2026-09-20T09:00:00Z", "claude-opus-5-5", contract("S"), out=900),   # 16일 전(7일 밖)
    ])
    # 같은 메시지가 두 줄로 나뉘고(스트리밍 중간값 포함) 다른 파일에 복제된 경우
    write(p / "s2.jsonl", [
        asst("m6", "2026-10-06T09:00:00Z", "claude-opus-5-5", "앞부분", out=50),
        asst("m6", "2026-10-06T09:00:01Z", "claude-opus-5-5", contract("D3"), out=400),
        asst("m1", "2026-10-06T10:00:00Z", "claude-sonnet-5-5", contract("D2"), out=500),  # s1 과 중복
    ])
    # 시험 세션
    write(p / "s3.jsonl", [
        user("RFP 해석해줘 (시험이므로 티어 판정 출력까지만 하고…)"),
        asst("m7", "2026-10-06T11:30:00Z", "claude-opus-5-5", contract("D3"), out=1000),
    ])
    # 인용만 있고 표지가 없는 메시지(판정으로 세면 안 됨)
    write(p / "s4.jsonl", [asst("m8", "2026-10-06T11:40:00Z", "claude-opus-5-5", "판정 티어: D3 라고 적혀 있다", out=10)])
    # 깨진 줄
    (p / "s5.jsonl").write_text("{깨진 json\n[]\n", encoding="utf-8")

    rc, out = run(["tiers", "--config-dir", str(cfg), "--now", NOW])
    n, tiers, saving, _ = ur.report_tiers(ur.collect([cfg], None, False)[0], ur.parse_ts(NOW) - ur.timedelta(days=7))
    check("tiers: 7일 안의 판정만 센다(D1 1·D2 1·D3 2, 16일 전 S 제외)",
          rc == 0 and n == 4 and dict(tiers) == {"D1": 1, "D2": 1, "D3": 2}, out)
    check("tiers: 절약 모드 ON/OFF 집계", dict(saving) == {"ON": 1, "OFF": 3}, str(dict(saving)))
    check("tiers: 중복 메시지·스트리밍 분할은 한 번만(m1, m6)", tiers["D2"] == 1 and tiers["D3"] == 2, str(dict(tiers)))
    check("tiers: 시험 세션 기본 제외(제외 1개 표시)", "제외 1개" in out and "D3 2" in out.replace("건", ""), out)
    rc, out_inc = run(["tiers", "--config-dir", str(cfg), "--now", NOW, "--include-tests"])
    check("tiers: --include-tests 면 시험 세션 포함(D3 3건)", "총 5건" in out_inc, out_inc)
    check("tiers: [Effort Router] 표지 없는 인용은 세지 않음", "총 4건" in out, out)

    rc, out_u = run(["usage", "--config-dir", str(cfg), "--now", NOW, "--hours", "5", "--days", "7"])
    recs, _ = ur.collect([cfg], None, False)
    per5 = ur.report_usage(recs, ur.parse_ts(NOW) - ur.timedelta(hours=5))
    check("usage: 5시간 창 — Opus 출력 2000+300+400+10(m8), Sonnet 500",
          per5["claude-opus-5-5"]["output_tokens"] == 2710 and per5["claude-sonnet-5-5"]["output_tokens"] == 500,
          str({k: v["output_tokens"] for k, v in per5.items()}))
    check("usage: 스트리밍 중간값 대신 최댓값(m6=400)·중복 파일은 한 번만(m1=500)·Opus 메시지 4개(m2,m3,m6,m8)",
          per5["claude-sonnet-5-5"]["messages"] == 1 and per5["claude-opus-5-5"]["messages"] == 4,
          str({k: v["messages"] for k, v in per5.items()}))
    per7 = ur.report_usage(recs, ur.parse_ts(NOW) - ur.timedelta(days=7))
    check("usage: 7일 창에는 5일 전 메시지 포함·16일 전 제외", per7["claude-opus-5-5"]["output_tokens"] == 3410,
          str(per7["claude-opus-5-5"]["output_tokens"]))
    check("usage: 시험 세션 토큰은 기본 제외", "시험 세션 제외 1개" in out_u or "제외 1개" in out_u, out_u)

    check("출력에 대화 내용이 나오지 않음(비밀 문자열·사용자 요청문)",
          SECRET not in out + out_inc + out_u and "견적서 만들어줘" not in out + out_u, "")

    rc, out_none = run(["tiers", "--config-dir", str(Path(t) / "없는폴더"), "--now", NOW])
    check("기록 폴더가 없어도 오류 없이 '기록 없음'", rc == 0 and "기록 없음" in out_none, out_none)
    rc, _ = run(["tiers", "--now", "엉터리"])
    check("--now 형식 오류는 종료코드 2", rc == 2, str(rc))

# ── 스킬 호출 집계(별도 합성 데이터 — 위 수치에 영향 없음) ──────────────────────────────────
ARGS_SECRET = "스킬인자-비밀내용-24680"


def call(mid, ts, skill, tid, tool="Skill"):
    return {"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "model": "claude-opus-5-5",
        "content": [{"type": "tool_use", "id": tid, "name": tool,
                     "input": {"skill": skill, "args": ARGS_SECRET}}],
        "usage": {"input_tokens": 1, "output_tokens": 1,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}


with tempfile.TemporaryDirectory() as t2:
    cfg2 = Path(t2) / "cfg2"
    q = cfg2 / "projects" / "p"
    write(q / "sA.jsonl", [user("요청 A"), call("a1", "2026-10-06T09:00:00Z", "effort-router", "tu1"),
                           asst("a2", "2026-10-06T09:01:00Z", "claude-opus-5-5", contract("D3"))])
    write(q / "sB.jsonl", [user("요청 B"), call("b1", "2026-10-06T09:10:00Z", "effort-router", "tu2"),
                           asst("b2", "2026-10-06T09:11:00Z", "claude-opus-5-5", "블록 없이 본문에서만 D3입니다")])
    write(q / "sC.jsonl", [user("요청 C"), call("c1", "2026-10-06T09:20:00Z", "klic-effort-router", "tu3"),
                           call("c2", "2026-10-06T09:21:00Z", "hwp", "tu4"),
                           call("c3", "2026-10-06T09:22:00Z", "effort-router", "tu7", tool="Bash")])  # Skill 이 아닌 도구
    write(q / "sD.jsonl", [call("a1", "2026-10-06T09:00:00Z", "effort-router", "tu1")])  # 이어하기 복제 — 중복
    write(q / "sE.jsonl", [user("요청 E (시험이므로 판정까지만)"), call("e1", "2026-10-06T09:30:00Z", "effort-router", "tu5")])
    write(q / "sF.jsonl", [user("요청 F"), call("f1", "2026-09-16T09:00:00Z", "effort-router", "tu6")])  # 20일 전

    rc, o = run(["tiers", "--config-dir", str(cfg2), "--now", NOW, "--days", "7"])
    check("스킬 호출: effort-router 2건·세션 2개(중복 복제·Bash 호출·시험 세션·20일 전 제외)",
          rc == 0 and "effort-router" in o and "   2건 (세션 2개" in o, o)
    check("스킬 호출: 호출은 있는데 판정 블록이 없는 세션 1개(sB)를 알려 줌",
          "판정 블록이 없는 세션 1개" in o and "과소집계" in o, o)
    check("스킬 호출: klic-effort-router 1건(별개 이름으로 센다)", "klic-effort-router    " in o and "   1건 (세션 1개" in o, o)
    check("스킬 호출: 판정 건수와 호출 건수가 따로 보임(판정 1건 < 호출 2건)", "총 1건" in o, o)

    rc, o_h = run(["tiers", "--config-dir", str(cfg2), "--now", NOW, "--skill", "hwp"])
    check("--skill 로 센 대상을 바꿀 수 있음(hwp 1건)", rc == 0 and "hwp" in o_h and "   1건 (세션 1개" in o_h, o_h)
    rc, o_t = run(["tiers", "--config-dir", str(cfg2), "--now", NOW, "--include-tests"])
    check("--include-tests 면 시험 세션의 호출도 셈(effort-router 3건)", "   3건 (세션 3개" in o_t, o_t)
    rc, o_30 = run(["tiers", "--config-dir", str(cfg2), "--now", NOW, "--days", "30"])
    check("--days 30 이면 20일 전 호출도 셈(effort-router 3건)", "   3건 (세션 3개" in o_30, o_30)
    check("스킬 호출 인자(args)의 내용은 출력에 나오지 않음", all(ARGS_SECRET not in x for x in (o, o_h, o_t, o_30)), "")

    rc, o_none = run(["tiers", "--config-dir", str(Path(t2) / "없음"), "--now", NOW])
    check("기록이 없으면 호출 0건으로 표시(오류 없음)", rc == 0 and "   0건 (세션 0개" in o_none, o_none)

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
