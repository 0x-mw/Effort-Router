#!/usr/bin/env python3
"""외부 보조 모델(Codex·Gemini 등) 전송 게이트 — fail-closed.

사용:
  ext_dispatch.py --task <작업유형> --file <경로> --target codex|gemini [--run] [--prompt "<지시>"]

기본은 판정만 한다(전송 안 함). 통과하면 exit 0, 차단이면 exit 2, 내부 오류도 차단(exit 1).
--run 은 target=codex 이고 codex 가 설치돼 있을 때만 stdin 으로 파일 내용을 넘긴다(미검증 경로).

차단 원칙: 허용 목록(ext_allowlist.json)에 작업유형·폴더·확장자가 모두 맞아야 통과한다.
폴더 목록이 비어 있으면 아무것도 통과하지 못한다. 내용 검사(PII·키 패턴)는 보조 수단이다.
로그에는 전송 내용을 남기지 않는다(시각·작업·대상·경로·판정·사유).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ALLOWLIST = Path(os.environ.get("EXT_ALLOWLIST") or SKILL_DIR / "ext_allowlist.json")
LOG = Path(os.environ.get("EXT_DISPATCH_LOG")
           or Path.home() / ".claude-mw/logs/ext_dispatch.jsonl")
TARGETS = {"codex", "gemini"}

CONTENT_PATTERNS = {
    "주민등록번호 형태": re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)"),
    "휴대전화 번호": re.compile(r"(?<!\d)01[016789][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)"),
    "이메일 주소": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "사업자등록번호 형태": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)"),
    "개인키 블록": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "API 키 형태": re.compile(r"\b(sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,})\b"),
}


class Deny(Exception):
    pass


def log(entry):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(LOG.parent, 0o700)
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 로그 실패가 판정을 바꾸지 않는다(차단 결정은 이미 났거나 통과해도 전송 내용은 안 남음)


def load_policy():
    try:
        policy = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
        tasks = policy["tasks"]
        deny_paths = [re.compile(p, re.I) for p in policy["deny_path_patterns"]]
        max_bytes = int(policy["max_bytes"])
    except (OSError, ValueError, KeyError, re.error) as e:
        raise Deny(f"허용 목록을 읽지 못함({type(e).__name__}) — 기본 차단")
    if not isinstance(tasks, dict):
        raise Deny("허용 목록 형식 오류 — 기본 차단")
    return tasks, deny_paths, max_bytes


def check(task, file_arg, target):
    """통과하면 (경로, 내용) 반환, 아니면 Deny."""
    if target not in TARGETS:
        raise Deny(f"알 수 없는 대상: {target}")
    tasks, deny_paths, max_bytes = load_policy()
    rule = tasks.get(task)
    if not isinstance(rule, dict):
        raise Deny(f"허용 목록에 없는 작업 유형: {task}")
    if target not in rule.get("targets", []):
        raise Deny(f"작업 '{task}'은 대상 '{target}' 전송이 허용되지 않음")

    raw = Path(file_arg)
    if raw.is_symlink():
        raise Deny("심볼릭 링크는 허용하지 않음")
    path = raw.resolve()
    if not path.is_file():
        raise Deny("일반 파일이 아님 또는 존재하지 않음")

    roots = [Path(r).expanduser().resolve() for r in rule.get("path_roots", [])]
    if not roots:
        raise Deny("허용 폴더가 등록되지 않음 — ext_allowlist.json 에 폴더를 직접 추가해야 열림")
    if not any(path == r or r in path.parents for r in roots):
        raise Deny("허용 폴더 밖의 파일")

    if path.suffix.lower() not in [e.lower() for e in rule.get("extensions", [])]:
        raise Deny(f"허용 확장자가 아님: {path.suffix or '(없음)'}")
    for pat in deny_paths:
        if pat.search(str(path)):
            raise Deny(f"금지 경로 패턴 일치: {pat.pattern}")

    size = path.stat().st_size
    if size > max_bytes:
        raise Deny(f"크기 초과: {size} > {max_bytes} bytes")
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        raise Deny(f"텍스트로 읽을 수 없음({type(e).__name__})")
    for label, pat in CONTENT_PATTERNS.items():
        if pat.search(text):
            raise Deny(f"내용에서 {label} 감지 — 삭제·마스킹 후 다시 시도")
    return path, text


def run_codex(text, prompt):
    exe = shutil.which("codex")
    if not exe:
        raise Deny("codex 가 설치되어 있지 않음 — --run 불가")
    # 셸을 거치지 않고 인자 목록으로 실행. 내용은 stdin 으로만 전달.
    p = subprocess.run([exe, "exec", prompt or "다음 내용을 검토하고 초안만 제시하라."],
                       input=text, capture_output=True, text=True, timeout=600)
    sys.stdout.write(p.stdout)
    return p.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--task", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--prompt", default="")
    a = ap.parse_args()

    rec = {"task": a.task, "target": a.target, "file": a.file, "run": a.run}
    try:
        path, text = check(a.task, a.file, a.target)
        rec["file"] = str(path)
        if a.run:
            if a.target != "codex":
                raise Deny(f"대상 '{a.target}'의 --run 은 미구현(호출 형식 미확인) — 판정만 가능")
            rec["decision"] = "allow+run"
            log(rec)
            return run_codex(text, a.prompt)
        rec["decision"] = "allow"
        log(rec)
        print(f"ALLOW {a.task} -> {a.target} ({path})")
        return 0
    except Deny as d:
        rec.update(decision="deny", reason=str(d))
        log(rec)
        print(f"DENY: {d}", file=sys.stderr)
        return 2
    except Exception as e:  # 예상 밖 오류도 차단으로 취급
        rec.update(decision="deny", reason=f"internal:{type(e).__name__}")
        log(rec)
        print(f"DENY(내부 오류): {type(e).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
