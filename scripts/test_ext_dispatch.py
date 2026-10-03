#!/usr/bin/env python3
"""ext_dispatch.py 시험 — 임시 폴더만 사용, 실제 외부 호출 없음."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent / "ext_dispatch.py")
REPO = Path(__file__).resolve().parent.parent
results = []


def policy():
    # 정책 파일에는 폴더가 없다 — 폴더는 local() 로 따로 만든다.
    return {
        "tasks": {"rfp_public_summary": {"targets": ["codex", "gemini"], "extensions": [".md", ".txt"]},
                  "draft_phrasing": {"targets": ["codex", "gemini"], "extensions": [".md", ".txt"]}},
        "deny_path_patterns": ["견적", "경력", "\\.env"],
        "max_bytes": 1000,
    }


def local(roots, task="rfp_public_summary"):
    return {"path_roots": {task: roots}}


def run(tmp, pol, loc, task, file, target, extra=()):
    """loc 이 None 이면 로컬 파일을 만들지 않는다(없는 상태로 시험)."""
    al = Path(tmp) / "allow.json"
    if pol is not None:
        al.write_text(pol if isinstance(pol, str) else json.dumps(pol), encoding="utf-8")
    lo = Path(tmp) / "allow.local.json"
    if loc is None:
        lo.unlink(missing_ok=True)
    else:
        lo.write_text(loc if isinstance(loc, str) else json.dumps(loc), encoding="utf-8")
    env = dict(os.environ, EXT_ALLOWLIST=str(al), EXT_ALLOWLIST_LOCAL=str(lo),
               EXT_DISPATCH_LOG=str(Path(tmp) / "log.jsonl"),
               PATH="/nonexistent")  # codex 가 PATH 에 없게
    p = subprocess.run([sys.executable, SCRIPT, "--task", task, "--file", str(file),
                        "--target", target, *extra], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def check(name, cond, detail=""):
    results.append(cond)
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else f"  → {detail}"))


with tempfile.TemporaryDirectory() as t:
    pub = Path(t) / "public"; pub.mkdir()
    good = pub / "rfp.md"; good.write_text("공개 RFP 본문입니다.", encoding="utf-8")
    pii = pub / "pii.md"; pii.write_text("담당자 900101-1234567", encoding="utf-8")
    phone = pub / "phone.md"; phone.write_text("연락처 010-1234-5678", encoding="utf-8")
    pdf = pub / "a.pdf"; pdf.write_bytes(b"%PDF")
    quote = pub / "견적_초안.md"; quote.write_text("본문", encoding="utf-8")
    big = pub / "big.md"; big.write_text("가" * 2000, encoding="utf-8")
    outside = Path(t) / "outside.md"; outside.write_text("본문", encoding="utf-8")
    link = pub / "link.md"; link.symlink_to(outside)
    P, L = policy(), local([str(pub)])

    rc, o = run(t, P, local([]), "rfp_public_summary", good, "codex")
    check("폴더 미등록이면 차단", rc == 2 and "허용 폴더가 등록되지 않음" in o, o)
    rc, o = run(t, P, L, "rfp_public_summary", good, "codex")
    check("허용 폴더의 깨끗한 md 는 통과", rc == 0 and o.startswith("ALLOW"), o)
    rc, o = run(t, P, L, "rfp_public_summary", pii, "codex")
    check("주민번호 형태 차단", rc == 2 and "주민등록번호" in o, o)
    rc, o = run(t, P, L, "rfp_public_summary", phone, "gemini")
    check("전화번호 차단", rc == 2 and "휴대전화" in o, o)
    rc, o = run(t, P, L, "rfp_public_summary", pdf, "codex")
    check("pdf 확장자 차단", rc == 2 and "확장자" in o, o)
    rc, o = run(t, P, L, "rfp_public_summary", quote, "codex")
    check("'견적' 경로 차단", rc == 2 and "금지 경로" in o, o)
    rc, o = run(t, P, L, "rfp_public_summary", big, "codex")
    check("크기 초과 차단", rc == 2 and "크기 초과" in o, o)
    rc, o = run(t, P, L, "rfp_public_summary", outside, "codex")
    check("허용 폴더 밖 차단", rc == 2 and "폴더 밖" in o, o)
    rc, o = run(t, P, L, "rfp_public_summary", link, "codex")
    check("심볼릭 링크 차단", rc == 2 and "심볼릭" in o, o)
    rc, o = run(t, P, L, "없는작업", good, "codex")
    check("목록에 없는 작업 차단", rc == 2 and "허용 목록에 없는" in o, o)
    rc, o = run(t, "{깨진 json", L, "rfp_public_summary", good, "codex")
    check("허용 목록이 깨지면 기본 차단", rc == 2 and "기본 차단" in o, o)
    rc, o = run(t, P, L, "rfp_public_summary", good, "gemini", ("--run",))
    check("gemini --run 은 미구현으로 차단", rc == 2 and "미구현" in o, o)
    rc, o = run(t, P, L, "rfp_public_summary", good, "codex", ("--run",))
    check("codex 미설치면 --run 차단", rc == 2 and "설치되어 있지 않음" in o, o)

    # 허용 폴더를 로컬 파일로 분리한 뒤의 시험
    PR = policy(); PR["tasks"]["rfp_public_summary"]["path_roots"] = [str(pub)]
    rc, o = run(t, PR, None, "rfp_public_summary", good, "codex")
    check("정책 파일의 path_roots 는 무시(로컬 없으면 차단)",
          rc == 2 and "허용 폴더가 등록되지 않음" in o and "path_roots 는 무시됨" in o, o)
    rc, o = run(t, P, None, "rfp_public_summary", good, "codex")
    check("로컬 파일이 없으면 차단", rc == 2 and "허용 폴더가 등록되지 않음" in o, o)
    rc, o = run(t, P, "{깨진 json", "rfp_public_summary", good, "codex")
    check("로컬 파일이 깨지면 기본 차단", rc == 2 and "로컬 허용 목록을 읽지 못함 — 기본 차단" in o, o)
    rc, o = run(t, P, {"path_roots": [str(pub)]}, "rfp_public_summary", good, "codex")
    check("로컬 path_roots 가 dict 가 아니면 기본 차단", rc == 2 and "기본 차단" in o, o)
    rc, o = run(t, P, {"path_roots": {"rfp_public_summary": str(pub)}}, "rfp_public_summary", good, "codex")
    check("로컬 작업 값이 list 가 아니면 기본 차단", rc == 2 and "기본 차단" in o, o)
    rc, o = run(t, P, local([""]), "rfp_public_summary", good, "codex")
    check("로컬 폴더에 빈 문자열이면 기본 차단", rc == 2 and "기본 차단" in o, o)
    other = local([str(pub)], task="draft_phrasing")
    rc, o = run(t, P, other, "rfp_public_summary", good, "codex")
    check("다른 작업에 등록된 폴더는 새지 않음(차단)", rc == 2 and "허용 폴더가 등록되지 않음" in o, o)
    rc, o = run(t, P, other, "draft_phrasing", good, "codex")
    check("같은 로컬 파일로 등록된 작업은 통과", rc == 0 and o.startswith("ALLOW"), o)

    log = (Path(t) / "log.jsonl").read_text(encoding="utf-8")
    check("로그에 전송 내용이 없음", "공개 RFP 본문" not in log and "900101" not in log, log[:200])

gi = REPO / ".gitignore"
if gi.is_file():
    lines = [x.strip() for x in gi.read_text(encoding="utf-8").splitlines()]
    check(".gitignore 에 ext_allowlist.local.json 있음", "ext_allowlist.local.json" in lines, str(gi))
else:
    print("SKIP .gitignore 없음 — ext_allowlist.local.json 제외 여부 확인 못 함(통과로 세지 않음)")

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
