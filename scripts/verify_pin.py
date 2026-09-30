#!/usr/bin/env python3
"""검증 핀 게이트 — SHA 핀·검증 입력 검사·선택적 명령 재실행.

검증 입력 자체가 변경·삭제·신규 무력화되어도 같은 입력으로 통과할 수 있는
자기참조 구멍과 이전 SHA 검증으로 새 코드가 통과하는 핀 부재를 기계적으로 드러낸다.
감지는 git의 커밋·staged·unstaged·삭제 diff, untracked·ignore-hidden 입력,
ls-files -v 은닉 표시와 사용자가 명시한 --verify-cmd 실행뿐이다. 기본 검사는 git
상태 읽기 전용이며 외부 전송·프로젝트 hook 자동 실행은 없다. --verify-cmd는 사용자가
지정한 명령 그대로 실행되고 작업 트리를 바꿀 수 있으므로 실행 전후 변화를 플래그한다.
--fresh-checkout은 비활성화되어 있고 어떤 worktree 생성·정리도 하지 않는다. Git 호출은
`GIT_OPTIONAL_LOCKS=0`, `-c core.autocrlf=false -c core.quotePath=false`,
`-c core.fsmonitor=false`를 고정한다.
종료코드: 0 = 관측된 플래그 없음(완료 근거는 caller가 판정), 1 = attention 플래그
(미해결 상태에서는 done 불가), 2 = 설정·환경·git·증거 저장 오류. exit 2는 검증
명령의 실패가 아니다 — 검증 명령 실패는 `verify_cmd_failed`와 exit 1로 구분된다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import verify_exec  # scripts/ 동일 디렉터리 국소 의존(§3-6 — r24 lib 패턴 준용)

GATE = 'verify-pin'
EXIT_PASS = 0
EXIT_ATTENTION = 1
EXIT_CONFIG = 2
DEFAULT_TIMEOUT = 600.0
GIT_FIXED = ('-c', 'core.autocrlf=false', '-c', 'core.quotePath=false',
             '-c', 'core.fsmonitor=false')

# 그룹 1 — 검증 입력(기본 8종, --pattern은 여기에만 append된다)
VERIFICATION_PATTERNS = ('tests/*', 'test/*', 'test_*.py', '*_test.py',
                         'conftest.py', 'pytest.ini', 'tox.ini', '.github/workflows/*')
# 그룹 2 — 다목적 설정(별도 플래그 — 검증 입력 신호 순도 유지, H5)
MULTIPURPOSE_PATTERNS = ('pyproject.toml', 'setup.cfg')

FLAG_SHA = 'sha_mismatch'
FLAG_INPUT = 'verification_input_modified'
FLAG_MULTIPURPOSE = 'multipurpose_config_modified'
FLAG_INPUT_HIDDEN = 'verification_input_hidden'
FLAG_INPUT_IGNORE_HIDDEN = 'verification_input_ignore_hidden'
FLAG_CMD_FAILED = 'verify_cmd_failed'
FLAG_CMD_TIMEOUT = 'verify_cmd_timeout'
# r26 신규 3종 — survivors는 재검증 계층(프로세스 정지 후 재실행으로 소멸 확인),
# head_moved·workspace_mutated는 정당화 계층(1회성 사건 — 재실행 소멸≠해제, M16)
FLAG_SURVIVORS = 'verify_cmd_survivors'
FLAG_HEAD_MOVED = 'head_moved_during_verify'
FLAG_WORKSPACE_MUTATED = 'verify_workspace_mutated'


class GateConfigError(Exception):
    """인자·env·git 오류 — exit 2(이유가 붙은 bypass)로 변환한다."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='검증 핀 게이트 — SHA 핀·검증 입력 분리·증거 기록(읽기 전용 결정론 게이트)')
    parser.add_argument('--base',
                        help='diff 기준 커밋(앵커 = 구현 착수 전 커밋) — 미지정 시 not_evaluated')
    parser.add_argument('--expect-sha',
                        help='직전 검증 실행이 기록한 HEAD SHA — 불일치 시 sha_mismatch')
    parser.add_argument('--verify-cmd',
                        help='재실행 검증명령(shlex 분할 — 셸 미경유, 필요 시 bash -c 전달)')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT,
                        help=f'verify-cmd 타임아웃 초(기본 {DEFAULT_TIMEOUT:.0f}, 양수 유한)')
    parser.add_argument('--pattern', action='append',
                        help='검증 입력 패턴 추가(반복 가능 — 기본 패턴에 append, '
                             'multipurpose 그룹 미적용)')
    parser.add_argument('--save',
                        help='결과 JSON 저장 디렉터리(실행별 고유 하위 폴더에 receipt.json 저장)')
    parser.add_argument('--fresh-checkout', action='store_true',
                        help='지원하지 않음: 안전한 worktree 소유·정리 계약이 없어 exit 2로 거부')
    return parser.parse_args(argv)


def validate_timeout(timeout: float) -> float:
    if not math.isfinite(timeout) or timeout <= 0:
        raise GateConfigError(f'--timeout은 양수 유한 값이어야 한다: {timeout!r}')
    return timeout


def run_git(*args: str) -> subprocess.CompletedProcess:
    """읽기 전용 git 호출 — optional locks·fsmonitor 비활성 + autocrlf·quotePath 고정.

    errors='replace' — 비UTF-8 파일명 바이트의 UnicodeDecodeError를 트레이스백 없이
    치환 문자로 흘려보낸다(④ 재리뷰 LOW: 엄격 디코드 실패는 JSON 없는 exit 1 오분류).
    """
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    return subprocess.run(['git', *GIT_FIXED, *args], capture_output=True, text=True,
                          errors='replace', env=env)


def fail_config(reason: str) -> None:
    print(f'FAIL verify pin: {reason}', file=sys.stderr)
    raise SystemExit(EXIT_CONFIG)


def git_stderr_reason(completed: subprocess.CompletedProcess) -> str:
    lines = completed.stderr.strip().splitlines()
    return lines[-1].strip() if lines else '(git 출력 없음)'


def resolve_head() -> str:
    completed = run_git('rev-parse', 'HEAD')
    if completed.returncode != 0:
        raise GateConfigError('git 저장소가 아니거나 커밋이 없다 — '
                              + git_stderr_reason(completed))
    return completed.stdout.strip()


def resolve_base(ref: str) -> str:
    completed = run_git('rev-parse', '--verify', ref)
    if completed.returncode != 0:
        raise GateConfigError(f'--base를 커밋으로 해석할 수 없다: {ref!r} — '
                              + git_stderr_reason(completed))
    return completed.stdout.strip()


def git_lines(*args: str) -> list[str]:
    completed = run_git(*args)
    if completed.returncode != 0:
        raise GateConfigError(f'git {args[0]} 실패 — {git_stderr_reason(completed)}')
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def collect_candidates(base_sha: str) -> list[str]:
    """앵커→작업 트리 후보 수집 — tracked diff(커밋·staged·unstaged·삭제) ∪ untracked 신규."""
    tracked = git_lines('diff', '--name-only', base_sha)
    untracked = git_lines('ls-files', '--others', '--exclude-standard')
    return sorted(set(tracked) | set(untracked))


def hidden_candidates() -> list[str]:
    """은닉 지정 경로 수집 — ls-files -v 태그 'h'(assume-unchanged)·'S'(skip-worktree).

    은닉 지정은 diff·status 양쪽에서 파일을 감춰 증거 워크플로 동반 마비시키는
    우회다(④ HIGH-1) — 후보 수집과 무관하게 항상 스캔한다.
    """
    paths = []
    for line in git_lines('ls-files', '-v'):
        tag, _, path = line.partition(' ')
        if tag in ('h', 'S') and path:
            paths.append(path)
    return paths


def ignored_untracked_candidates() -> list[str]:
    """ignore 은닉 스캔(r25 H4) 모수 — untracked∧ignored 차집합.

    `git ls-files --others`(제외 없음) ∖ `--exclude-standard` = .gitignore·
    .git/info/exclude·전역 excludesFile 제외 untracked — diff·untracked 검출이
    모두 놓치는 우회 모수다. 무제외 스캔은 venv·node_modules 규모(수만 파일)에서
    수백 ms~수 초(V10·A14) — verify-cmd 경로와 합산해 상한 없이 수용한다.
    """
    all_others = git_lines('ls-files', '--others')
    standard = git_lines('ls-files', '--others', '--exclude-standard')
    return sorted(set(all_others) - set(standard))


def match_full_paths(candidates: list[str], patterns: tuple[str, ...]) -> list[str]:
    """전체경로 fnmatchcase 한정 매칭 — basename 매칭 금지(RC-1).

    차집합 모수는 의존성 트리(.venv*/.../site-packages/tests/test_*.py)를 포함해
    basename 매칭이면 관행 패턴과 결합해 의존성 테스트를 오탐한다(본 저장소 실측
    18힛) — 패턴은 경로 선두부터 fnmatch로 판정한다. 임의 깊이 무력화 파일 보완은
    --pattern 확장 계약(A15).
    """
    return sorted(path for path in candidates
                  if any(fnmatchcase(path, pattern) for pattern in patterns))


def hidden_patterns(extra_patterns: list[str]) -> tuple[str, ...]:
    """은닉 매칭 패턴 — 검증 입력(기본+추가)과 multipurpose 양 그룹 통합(④ 재리뷰 HIGH).

    그룹 2 은닉은 pyproject 무력화 직통 우회다 — 은닉은 어느 그룹이든 즉시 의심 신호이므로
    그룹 구분 없이 단일 플래그로 발행한다.
    """
    return (*VERIFICATION_PATTERNS, *extra_patterns, *MULTIPURPOSE_PATTERNS)


def match_candidates(candidates: list[str], patterns: tuple[str, ...]) -> list[str]:
    """fnmatchcase(경로 전체) 또는 fnmatchcase(basename) — 플랫폼 대소문자 무관 결정론."""
    return sorted(path for path in candidates
                  if any(fnmatchcase(path, pattern) or fnmatchcase(Path(path).name, pattern)
                         for pattern in patterns))


def inspect_verification(base_sha: str | None,
                         extra_patterns: list[str]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """검증 입력 그룹(기본+추가)과 multipurpose 그룹을 분리 발행한다(H5).

    base 미지정은 not_evaluated — 미검사 ≠ 변경 없음(M-d). 패턴 비통과 후보는 폐기한다
    (untracked 오탐 폭주 방지 — H1 LOW). 은닉 지정 검출(파이프라인 4단계)과 ignore
    은닉 검출(r25)은 base와 무관하게 항상 수행하며 양 그룹 패턴을 통합 적용한다
    (④ 재리뷰 HIGH·r25 H4).
    """
    patterns = (*VERIFICATION_PATTERNS, *extra_patterns)
    hidden = match_candidates(hidden_candidates(), hidden_patterns(extra_patterns))
    ignore_hidden = match_full_paths(ignored_untracked_candidates(),
                                     hidden_patterns(extra_patterns))
    if base_sha is None:
        return {'status': 'not_evaluated', 'patterns': list(patterns),
                'modified_files': [], 'multipurpose_files': [],
                'hidden_files': hidden, 'ignore_hidden_files': ignore_hidden}, \
            ((FLAG_INPUT_HIDDEN,) if hidden else ()) \
            + ((FLAG_INPUT_IGNORE_HIDDEN,) if ignore_hidden else ())
    candidates = collect_candidates(base_sha)
    modified = match_candidates(candidates, patterns)
    multipurpose = match_candidates(candidates, MULTIPURPOSE_PATTERNS)
    verification = {'status': 'modified' if modified else 'clean',
                    'patterns': list(patterns),
                    'modified_files': modified,
                    'multipurpose_files': multipurpose,
                    'hidden_files': hidden,
                    'ignore_hidden_files': ignore_hidden}
    flags = ((FLAG_INPUT,) if modified else ()) \
        + ((FLAG_MULTIPURPOSE,) if multipurpose else ()) \
        + ((FLAG_INPUT_HIDDEN,) if hidden else ()) \
        + ((FLAG_INPUT_IGNORE_HIDDEN,) if ignore_hidden else ())
    return verification, flags


def run_verify_window(command: str, timeout: float, process_group: bool,
                      patterns: tuple[str, ...],
                      cwd: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """verify-cmd 실행창 — 직전 스냅샷 → 실행 → 직후 재판정 델타(r26 §5.1).

    기준은 실행 창 전후 델타(게이트 시작 아님) — 선행 inspect_verification과
    무관하다. 사전 dirty는 플래그 대상 아니다(게이트의 검증입력 검사 영역).
    untracked 신규·소실은 검증입력 패턴 매칭 한정 검출(.pytest_cache 등 산출물
    오탐 방지 — §12-3 승인 트레이드오프), exclude 내용 변화는 무조건 변형이다(M8).
    ps 실패 등 엔진 오류는 verify_exec.GateConfigError — 호출부가 exit 2로
    변환한다(H2)."""
    before = verify_exec.window_snapshot(patterns)  # 사용자가 지정한 명령 실행 전 작업 트리
    verify_cmd = verify_exec.run_verify_cmd(command, timeout,
                                            cwd or Path.cwd(), process_group)
    delta = verify_exec.window_delta(before, verify_exec.window_snapshot(patterns))
    untracked_matched = match_candidates(
        delta['untracked_new'] + delta['untracked_gone'] + delta['untracked_changed'], patterns)
    detail = {**delta, 'untracked_matched': untracked_matched}
    return {**verify_cmd, 'window_delta': detail}, untracked_matched


def verify_window_flags(delta: dict[str, Any]) -> tuple[str, ...]:
    """실행창 델타 플래그 — HEAD 이동은 head_moved_during_verify(정당화 계층),
    tracked 변형·exclude 변화·패턴 매칭 untracked는 verify_workspace_mutated
    (정당화 계층 — 1회성 사건, 재실행 소멸≠해제, M16)."""
    flags = ()
    if delta['head_moved']:
        flags += (FLAG_HEAD_MOVED,)
    if delta['tracked_changed'] or delta['index_changed'] or delta['exclude_changed'] \
            or delta['untracked_matched']:
        flags += (FLAG_WORKSPACE_MUTATED,)
    return flags


def verify_cmd_flags(verify: dict[str, Any]) -> tuple[str, ...]:
    flags = ()
    if verify['timed_out']:
        flags += (FLAG_CMD_TIMEOUT,)  # 타임아웃이 원인 — failed 병기 금지(배타성, LOW)
    elif verify['exit_code'] != 0:
        flags += (FLAG_CMD_FAILED,)
    if verify.get('survivors'):
        flags += (FLAG_SURVIVORS,)  # 독립 축(프로세스 잔존) — timeout과 병기 가능(r26)
    return flags


def assemble_result(head_sha: str, expect_sha: str | None, base_ref: str | None,
                    base_sha: str | None, sha_matched: bool | None,
                    verification: dict[str, Any], verify_cmd: dict[str, Any] | None,
                    flags: list[str],
                    fresh: dict[str, Any] | None = None) -> dict[str, Any]:
    """결과 dict 새 조립 — 돌연변이 없다(번들 §5 구현 노트)."""
    return {'ok': len(flags) == 0,
            'gate': GATE,
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'head_sha': head_sha,
            'pin': {'expect_sha': expect_sha, 'base_ref': base_ref,
                    'base_sha': base_sha, 'sha_matched': sha_matched},
            'verification_input': verification,
            'verify_cmd': verify_cmd,
            'fresh': fresh,
            'flags': list(flags),
            'saved_to': None}


class ReceiptWriter:
    """증분 영수증 — 실행마다 고유 하위 폴더를 만들고 원자 기록한다.

    새 실행은 기존 영수증이나 임시 파일을 검색·삭제하지 않는다. `mkdtemp`가
    저장 디렉터리 아래 독립된 시도 폴더를 만들므로 동시 실행의 파일 경로도 분리된다.
    실패 종료(exit 2) 시 마지막 성공 stage를 보존하고, 중간 저장 실패 뒤에도 검사는
    계속한다 — 최종 저장까지 실패하면 stdout JSON과 exit 2로 저장 오류를 알린다.
    """

    def __init__(self, save_dir: str):
        self.failed_reason: str | None = None
        self.path: Path | None = None
        self._tmp: Path | None = None
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        try:
            directory = Path(save_dir)
            directory.mkdir(parents=True, exist_ok=True)
            attempt_dir = Path(tempfile.mkdtemp(prefix=f'{GATE}-{stamp}-', dir=directory))
            self.path = attempt_dir / 'receipt.json'
            self._tmp = attempt_dir / 'receipt.json.tmp'
        except OSError as error:
            self.failed_reason = str(error)

    def write(self, stage: str, result: dict[str, Any]) -> None:
        """단계 완료 시점에 영수증 전체를 원자 재기록한다."""
        if self.failed_reason is not None:
            return
        assert self.path is not None and self._tmp is not None
        payload = {**result, 'stage': stage, 'saved_to': str(self.path)}
        try:
            with open(self._tmp, 'x', encoding='utf-8') as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(self._tmp, self.path)
        except OSError as error:
            self.failed_reason = str(error)

    def finish(self, result: dict[str, Any]) -> str | None:
        """최종 complete 기록 — 성공 시 경로, 실패 시 None."""
        if self.failed_reason is None:
            self.write('complete', result)
        return None if self.failed_reason is not None else str(self.path)


def write_stage(writer: ReceiptWriter | None, stage: str,
                result: dict[str, Any]) -> None:
    """영수증 단계 기록 — writer 없으면 no-op(--save 미지정은 파일 0생성 계약)."""
    if writer is not None:
        writer.write(stage, result)


def execute_verify_window(args: argparse.Namespace, timeout: float
                          ) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """사용자가 지정한 명령의 실행창을 검사한다. ps 등 엔진 오류는 exit 2다."""
    if args.verify_cmd is None:
        return None, ()
    patterns = (*VERIFICATION_PATTERNS, *(args.pattern or []))
    try:
        verify_cmd, _ = run_verify_window(args.verify_cmd, timeout,
                                          verify_exec.capability(), patterns)
    except (GateConfigError, verify_exec.GateConfigError) as error:
        fail_config(str(error))
    return verify_cmd, verify_window_flags(verify_cmd['window_delta'])


def finish_receipt(writer: ReceiptWriter | None,
                   result: dict[str, Any]) -> dict[str, Any]:
    """최종 저장 경로 — 저장 실패는 stdout 보존 후 exit 2(§3-2·§5.3 계약)."""
    if writer is None:
        return result
    saved = writer.finish(result)
    if saved is None:
        print(json.dumps(result, ensure_ascii=False))
        fail_config('증분 영수증 기록 실패 후 최종 저장도 실패 — 검사 결과는 위 '
                    f'stdout JSON에 보존됐다: {writer.failed_reason}')
    return {**result, 'saved_to': saved}


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.fresh_checkout:
        fail_config('--fresh-checkout은 비활성화되어 있다 — 고정 worktree 경로의 안전한 '
                    '소유·정리 계약이 없어 worktree를 생성하거나 삭제하지 않는다')
    writer = ReceiptWriter(args.save) if args.save is not None else None
    try:
        timeout = validate_timeout(args.timeout)
        head = resolve_head()
        base_sha = resolve_base(args.base) if args.base is not None else None
        sha_matched = None if args.expect_sha is None else head == args.expect_sha
    except (GateConfigError, verify_exec.GateConfigError) as error:
        fail_config(str(error))

    def snap(verification: dict[str, Any] | None = None,
             verify_cmd: dict[str, Any] | None = None,
             flags: list[str] | None = None) -> dict[str, Any]:
        """현재까지의 구성 요소로 부분 결과 조립 — 영수증 단계 기록 재료."""
        return assemble_result(head, args.expect_sha, args.base, base_sha,
                               sha_matched, verification, verify_cmd,
                               flags if flags is not None else [])

    write_stage(writer, 'init', snap())
    try:
        verification, verification_flags = inspect_verification(base_sha, args.pattern or [])
    except GateConfigError as error:
        fail_config(str(error))
    sha_flags = (FLAG_SHA,) if sha_matched is False else ()
    write_stage(writer, 'inspected',
                snap(verification, flags=[*sha_flags, *verification_flags]))
    verify_cmd, window_flags = execute_verify_window(args, timeout)
    cmd_flags = verify_cmd_flags(verify_cmd) if verify_cmd is not None else ()
    flags = [*sha_flags, *verification_flags, *cmd_flags, *window_flags]
    result = snap(verification, verify_cmd, flags)
    write_stage(writer, 'verify_cmd', result)
    result = finish_receipt(writer, result)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(EXIT_PASS if not result['flags'] else EXIT_ATTENTION)

if __name__ == '__main__':
    main()
