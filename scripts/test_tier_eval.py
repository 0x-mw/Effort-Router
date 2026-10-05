#!/usr/bin/env python3
"""tier_eval.py 오프라인 시험 — 모델 호출 없음(파싱·채점·사례 검증·--dry-run 만)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tier_eval as te  # noqa: E402

SCRIPT = str(HERE / "tier_eval.py")
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else f"  → {detail}"))


def contract(tier, saving="OFF"):
    # 가짜 응답 — Output Contract 블록 + 본문
    return f"```text\n[Effort Router]\n- 판정 티어: {tier}\n- 절약 모드: {saving}\n```\n\n판정 근거 본문"


CASE = {"id": "X1", "request": "가짜 요청", "expected_tiers": ["D3"], "expect_saving": None, "note": ""}

check("정상 파싱", te.parse_tier(contract("D2")) == "D2" and te.parse_tier("- 판정 티어: XL") == "XL"
      and te.parse_tier("판정 티어: 보안감사") == "보안감사", te.parse_tier(contract("D2")))
check("티어 줄 없음 → None", te.parse_tier("D3 로 보입니다. 티어는 S 입니다.") is None
      and te.parse_tier("") is None and te.parse_tier(None) is None)
t = "S 티어로 볼 수도 있지만 상위를 택함\n- 판정 티어: D3\nS·M·L 은 코드 티어"
check("D3 와 S 혼동 없음", te.parse_tier(t) == "D3" and te.parse_tier("판정 티어: S") == "S"
      and te.parse_tier("판정 티어: SM") is None and te.parse_tier("판정 티어: D4") is None, te.parse_tier(t))
check("절약 ON 파싱", te.parse_saving(contract("D3", "ON")) == "ON")
check("절약 OFF 파싱, 블록 밖 줄은 무시",
      te.parse_saving(contract("D3", "OFF")) == "OFF"
      and te.parse_saving("절약 모드: ON\n```text\n- 판정 티어: D1\n```") is None)
r = te.score(CASE, contract("D3"), ["Skill", "Bash", "Read"])
check("도구 위반 탐지(UNSAFE_TOOL)", not r["ok"] and r["reason"].startswith("UNSAFE_TOOL") and "Bash" in r["reason"], r)
r = te.score(CASE, contract("D3"), ["Skill", "Read"])
check("허용 도구·기대 티어면 통과", r["ok"] and r["tier"] == "D3", r)
r = te.score(CASE, "본문에 D3 글자만 있음", ["Skill"])
check("티어 줄 없으면 포함만으로 통과하지 않음", not r["ok"] and r["reason"] == "NO_TIER", r)
r = te.score(dict(CASE, expected_tiers=["D1"]), contract("D3"), ["Skill"])
check("다른 티어는 WRONG_TIER", not r["ok"] and r["reason"] == "WRONG_TIER", r)
r = te.score(dict(CASE, expect_saving=True), contract("D3", "OFF"), ["Skill"])
check("절약 ON 기대인데 OFF 면 실패", not r["ok"] and r["reason"] == "SAVING_NOT_ON", r)

cases = json.loads((HERE / "tier_eval_cases.json").read_text(encoding="utf-8"))
errs = te.validate_cases(cases)
check("사례 파일 검증 통과(15건)", not errs and len(cases) == 15, errs)
dup = cases + [dict(cases[0])]
check("중복 id 검출", any("중복 id" in e for e in te.validate_cases(dup)), te.validate_cases(dup))
bad = [dict(CASE, expected_tiers=["D4"])]
check("알 수 없는 티어 검출", any("알 수 없는 티어" in e for e in te.validate_cases(bad)), te.validate_cases(bad))
miss = [{k: v for k, v in CASE.items() if k != "expected_tiers"}]
check("필수 키 누락 검출", any("필수 키" in e for e in te.validate_cases(miss)), te.validate_cases(miss))

# --dry-run 은 claude 를 부르지 않는다 — PATH 를 비워 claude 가 있어도 못 찾게 한다
env = dict(os.environ, PATH="/nonexistent", PYTHONDONTWRITEBYTECODE="1")
with tempfile.TemporaryDirectory() as tmp:
    p = subprocess.run([sys.executable, SCRIPT, "--dry-run", "--out", str(Path(tmp) / "o")],
                       capture_output=True, text=True, env=env, cwd=tmp)
    check("--dry-run 종료코드 0, 15건 표", p.returncode == 0 and "T15" in p.stdout and "사례 15건" in p.stdout
          and not (Path(tmp) / "o").exists(), p.stdout + p.stderr)
    badf = Path(tmp) / "bad.json"
    badf.write_text(json.dumps(dup, ensure_ascii=False), encoding="utf-8")
    p = subprocess.run([sys.executable, SCRIPT, "--dry-run", "--cases", str(badf)],
                       capture_output=True, text=True, env=env, cwd=tmp)
    check("--dry-run 형식 오류면 종료코드 2", p.returncode == 2 and "중복 id" in p.stderr, p.stdout + p.stderr)

    # 실행 경로는 진짜 claude 대신 가짜 스텁으로 본다 — PATH 를 스텁 폴더만으로 제한
    stub_dir = Path(tmp) / "bin"; stub_dir.mkdir()
    stub = stub_dir / "claude"
    stub.write_text(f"""#!{sys.executable}
import json, sys
req = sys.argv[2]
tool = "Bash" if "도구위반" in req else "Skill"
print(json.dumps({{"type": "assistant", "message": {{"content": [
    {{"type": "tool_use", "name": tool, "input": {{"skill": "effort-router"}}}}]}}}}))
print(json.dumps({{"type": "result", "result": "```text\\n- 판정 티어: D3\\n- 절약 모드: OFF\\n```"}}))
""", encoding="utf-8")
    stub.chmod(0o755)
    fake = [dict(CASE, id="F1"), dict(CASE, id="F2", request="도구위반 가짜 요청")]
    ff = Path(tmp) / "fake.json"; ff.write_text(json.dumps(fake, ensure_ascii=False), encoding="utf-8")
    out = Path(tmp) / "out"
    senv = dict(env, PATH=str(stub_dir))
    common = ["--cases", str(ff), "--out", str(out), "--config-dir", str(Path(tmp) / "cfg"), "--workdir", tmp]
    p = subprocess.run([sys.executable, SCRIPT, *common, "--only", "F1"],
                       capture_output=True, text=True, env=senv, cwd=tmp)
    s = json.loads((out / "summary.json").read_text(encoding="utf-8")) if (out / "summary.json").exists() else []
    check("스텁 실행: 통과 사례는 종료코드 0·summary 저장",
          p.returncode == 0 and s and s[0]["ok"] and s[0]["skills"] == ["effort-router"]
          and (out / "F1.txt").exists(), p.stdout + p.stderr)
    p = subprocess.run([sys.executable, SCRIPT, *common], capture_output=True, text=True, env=senv, cwd=tmp)
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    check("스텁 실행: 도구 위반 사례가 있으면 종료코드 1",
          p.returncode == 1 and [x["ok"] for x in s] == [True, False]
          and s[1]["reason"].startswith("UNSAFE_TOOL"), p.stdout + p.stderr)

    # --repeat: 일관성(같은 사례 N회) — 모두 통과해야 통과, 판정이 갈리면 흔들림 표시
    out2 = Path(tmp) / "out_rep"
    rc2 = ["--cases", str(ff), "--out", str(out2), "--config-dir", str(Path(tmp) / "cfg"), "--workdir", tmp]
    p = subprocess.run([sys.executable, SCRIPT, *rc2, "--only", "F1", "--repeat", "2"],
                       capture_output=True, text=True, env=senv, cwd=tmp)
    s2 = json.loads((out2 / "summary.json").read_text(encoding="utf-8"))
    check("--repeat 2: 일관되면 통과·회차별 파일 저장",
          p.returncode == 0 and s2[0]["runs"] == 2 and s2[0]["passed_runs"] == 2 and not s2[0]["flaky"]
          and (out2 / "F1_1.txt").exists() and (out2 / "F1_2.txt").exists(), p.stdout + p.stderr)

    flip_dir = Path(tmp) / "flipbin"; flip_dir.mkdir()
    state = Path(tmp) / "flip.state"
    flip = flip_dir / "claude"
    flip.write_text(f"""#!{sys.executable}
import json, sys
st = {str(state)!r}
try: n = int(open(st).read())
except OSError: n = 0
open(st, "w").write(str(n + 1))
tier = "D3" if n == 0 else "D2"
print(json.dumps({{"type": "assistant", "message": {{"content": [
    {{"type": "tool_use", "name": "Skill", "input": {{"skill": "effort-router"}}}}]}}}}))
print(json.dumps({{"type": "result", "result": "```text\\n- 판정 티어: " + tier + "\\n- 절약 모드: OFF\\n```"}}))
""", encoding="utf-8")
    flip.chmod(0o755)
    out3 = Path(tmp) / "out_flip"
    p = subprocess.run([sys.executable, SCRIPT, "--cases", str(ff), "--out", str(out3), "--only", "F1",
                        "--repeat", "2", "--jobs", "1", "--config-dir", str(Path(tmp) / "cfg"), "--workdir", tmp],
                       capture_output=True, text=True, env=dict(env, PATH=str(flip_dir)), cwd=tmp)
    s3 = json.loads((out3 / "summary.json").read_text(encoding="utf-8"))
    check("--repeat 2: 판정이 갈리면 실패·흔들림 표시",
          p.returncode == 1 and s3[0]["flaky"] and s3[0]["tiers"] == ["D3", "D2"] and not s3[0]["ok"]
          and "흔들림" in p.stdout, p.stdout + p.stderr)

    empty = Path(tmp) / "emptybin"; empty.mkdir()
    out4 = Path(tmp) / "out_missing"
    p = subprocess.run([sys.executable, SCRIPT, "--cases", str(ff), "--out", str(out4), "--only", "F1",
                        "--config-dir", str(Path(tmp) / "cfg"), "--workdir", tmp],
                       capture_output=True, text=True,
                       env=dict(env, PATH=str(empty), TIER_EVAL_RETRY_WAIT="0"), cwd=tmp)
    s4 = json.loads((out4 / "summary.json").read_text(encoding="utf-8"))
    check("실행 파일이 없으면 1회 재시도 후 실패 처리(무한 반복 없음)",
          p.returncode == 1 and not s4[0]["ok"] and "FileNotFoundError" in s4[0]["reason"], p.stdout + p.stderr)

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
