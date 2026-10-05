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

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
