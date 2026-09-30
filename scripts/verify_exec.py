#!/usr/bin/env python3
"""검증 실행 엔진 — 프로세스 그룹 제어와 verify-window.

verify_pin.py(CLI)가 임포트하는 실행 계층이다. 설계 계약은 verify_pin.py와
SKILL.md '검증 핀 게이트' 절에 있다. 원형: todo-flow verification.py stop_group
(어휘 이식 — SIGTERM→pgid 폴링 1.5s→SIGKILL→드레인→2s 폴링, ps 실패 예외 보관·
재발기). 엔진은 git 상태를 읽고 실행창 변화를 기록한다. 사용자가 명시한
--verify-cmd는 작업 트리를 변경할 수 있다. 메인 워크스페이스 tracked·인덱스·HEAD를
직접 기록하지 않는다. 한계: 프로세스 그룹 제어는 같은 pgid에 머무는 자손 한정 —
setsid 등 신규 pgid 생성 이탈 자손은 killpg·ps 폴링 모두 범위 밖이다.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import stat
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

EXIT_CONFIG = 2
GIT_FIXED = ('-c', 'core.autocrlf=false', '-c', 'core.quotePath=false',
             '-c', 'core.fsmonitor=false')
TAIL_CHARS = 500
# stop_group — SIGTERM 후 그룹 사멸 폴링(Slow teardown의 coverage flush 보호, r26 M13)
TERM_POLL_S = 1.5
# SIGKILL 후 잔존 관측 상한 — 초과 시 undead 종착지(survivors 플래그 확정 후 진행, M2)
KILL_POLL_S = 2.0
# 파이프 드레인 상한 — 파이프 잡은 생존자 잔존 시 fd close + wait 회수로 탈출(P6)
DRAIN_S = 5.0
# 정상 종료 후 생존자 판정 — 0.3s deadline 지속 관측 + 안정화 재스캔 1회(M14)
SURVIVOR_WINDOW_S = 0.3
SURVIVOR_SETTLE_S = 0.05


class GateConfigError(Exception):
    """인자·env·git·ps 오류 — exit 2(이유가 붙은 bypass)로 변환한다."""


class ExecPsError(GateConfigError):
    """실행 중 ps 실패(H2) — 생존자 판정 불능. 조용한 통과 금지, exit 2로 변환."""


# ---------------------------------------------------------------- git 하층

def run_git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """읽기 전용 git 호출 — optional locks·fsmonitor 비활성, 인용·정규화·비UTF-8 방어."""
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    return subprocess.run(['git', *GIT_FIXED, *args], capture_output=True,
                          text=True, errors='replace', cwd=cwd, env=env)


def git_reason(completed: subprocess.CompletedProcess) -> str:
    lines = completed.stderr.strip().splitlines()
    return lines[-1].strip() if lines else '(git 출력 없음)'


def git_ok(*args: str, cwd: str | None = None) -> str:
    completed = run_git(*args, cwd=cwd)
    if completed.returncode != 0:
        raise GateConfigError(f'git {args[0]} 실패 — {git_reason(completed)}')
    return completed.stdout.strip()


# ------------------------------------------------------------ capability probe

def capability() -> bool:
    """게이트 시작 시 1회 — hasattr(killpg) ∧ which(ps) 양측 참이면 posix-killpg."""
    return hasattr(os, 'killpg') and shutil.which('ps') is not None


# ------------------------------------------------- 프로세스 그룹(todo-flow 원형)

def group_running(pgid: int) -> bool:
    """그룹 생존 판정 — ps -axo pgid=,stat= 폴링(좀비 Z 제외). ps 실패는 H2 예외."""
    try:
        result = subprocess.run(['ps', '-axo', 'pgid=,stat='], capture_output=True,
                                text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExecPsError('생존자 판정 불능 — ps 실행 실패: '
                          f'{type(error).__name__}: {error}') from error
    if result.returncode != 0:
        raise ExecPsError('생존자 판정 불능 — ps 실패: '
                          f'{(result.stderr or result.stdout).strip()[:200]}')
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == str(pgid) and not fields[1].startswith('Z'):
            return True
    return False


def await_group_death(pgid: int, timeout_s: float) -> bool:
    """그룹 사멸 폴링 — deadline 내 사멸 True·초과 False(undead). ps 실패는 재발행."""
    deadline = time.monotonic() + timeout_s
    while True:
        if not group_running(pgid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def _send_group_signal(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return  # 그룹 소멸 — 목적 달성
    except PermissionError as error:
        try:
            running = group_running(pgid)
        except ExecPsError as status_error:
            raise GateConfigError(f'프로세스 그룹 신호 거부 후 생존 여부를 확인할 수 없다 '
                                  f'(pgid={pgid}, signal={sig}) — {error}; '
                                  f'{status_error}') from error
        if not running:
            return  # 신호와 상태 확인 사이에 그룹이 사라졌다
        raise GateConfigError(f'살아 있는 프로세스 그룹에 신호를 보낼 수 없다 '
                              f'(pgid={pgid}, signal={sig}) — {error}') from error
    except OSError as error:
        raise GateConfigError(f'프로세스 그룹 신호 실패 '
                              f'(pgid={pgid}, signal={sig}) — {error}') from error


def _drain_after_group_death(proc: subprocess.Popen) -> tuple[str, str]:
    """그룹 사멸 후 드레인(순서 강제 — P6 교착 방지). 드레인 타임아웃 시 fd close +
    wait(5)로 직속 자식 회수 — 이 경로에서는 출력 tail이 소실될 수 있다(LOW —
    파이프를 놓는 대가)."""
    try:
        return proc.communicate(timeout=DRAIN_S)
    except subprocess.TimeoutExpired:
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=DRAIN_S)
        except subprocess.TimeoutExpired:
            pass  # 직속 자식 미회수 — survivors 판정(아래)이 잔존을 보고한다
        except OSError as error:
            raise GateConfigError(f'검증 자식 회수 실패 — {error}') from error
        return '', ''
    except OSError as error:
        raise GateConfigError(f'검증 자식 출력 회수 실패 — {error}') from error


def stop_group(proc: subprocess.Popen) -> tuple[str, str, bool]:
    """그룹 종료 — SIGTERM→사멸 확인, 살아 있거나 미확인이면 SIGKILL→드레인.

    반환 (stdout, stderr, group_dead). 이미 사멸한 그룹에 SIGKILL을 재전송하지 않는다.
    ps·신호·직속 자식 회수 오류는 exit 2용 GateConfigError로 표면화한다."""
    signal_errors: list[GateConfigError] = []
    ps_errors: list[ExecPsError] = []
    try:
        _send_group_signal(proc.pid, signal.SIGTERM)
    except GateConfigError as error:
        signal_errors.append(error)
    try:
        term_dead = await_group_death(proc.pid, TERM_POLL_S)
    except ExecPsError as error:
        ps_errors.append(error)
        term_dead = False  # unknown is treated as live for the bounded SIGKILL attempt
    if not term_dead:
        try:
            _send_group_signal(proc.pid, signal.SIGKILL)
        except GateConfigError as error:
            signal_errors.append(error)
    stdout, stderr = _drain_after_group_death(proc)
    if proc.poll() is None:
        raise GateConfigError('프로세스 그룹 정리 후 직속 자식을 제한 시간 안에 회수하지 못했다')
    if term_dead:
        group_dead = True
    else:
        try:
            group_dead = await_group_death(proc.pid, KILL_POLL_S)
        except ExecPsError as error:
            raise GateConfigError(f'프로세스 그룹 정리 후 생존 여부를 확인할 수 없다 — '
                                  f'{error}') from error
    if ps_errors:
        raise GateConfigError(f'프로세스 그룹 정리 중 생존 여부 확인에 실패했다 — '
                              + '; '.join(str(error) for error in ps_errors))
    if signal_errors and not group_dead:
        raise GateConfigError('프로세스 그룹 신호가 거부됐고 그룹 생존 여부가 해소되지 않았다 — '
                              + '; '.join(str(error) for error in signal_errors))
    return stdout, stderr, group_dead


def detect_survivors(pgid: int) -> bool:
    """정상 종료 후 생존자 판정 — 0.3s deadline 폴링에서 지속 관측될 때만 성립
    (직속 종료 레이스의 순간 오탐 방지) + 판정 전 안정화 재스캔 1회(M14)."""
    deadline = time.monotonic() + SURVIVOR_WINDOW_S
    while True:
        if not group_running(pgid):
            return False
        if time.monotonic() >= deadline:
            time.sleep(SURVIVOR_SETTLE_S)
            return group_running(pgid)
        time.sleep(0.02)


def terminate_group(pgid: int) -> bool:
    """생존 그룹 종료 — SIGTERM 뒤 사멸을 확인하고 필요할 때만 SIGKILL한다."""
    signal_errors: list[GateConfigError] = []
    ps_errors: list[ExecPsError] = []
    try:
        _send_group_signal(pgid, signal.SIGTERM)
    except GateConfigError as error:
        signal_errors.append(error)
    try:
        term_dead = await_group_death(pgid, TERM_POLL_S)
    except ExecPsError as error:
        ps_errors.append(error)
        term_dead = False
    if term_dead:
        return True
    try:
        _send_group_signal(pgid, signal.SIGKILL)
    except GateConfigError as error:
        signal_errors.append(error)
    try:
        group_dead = await_group_death(pgid, KILL_POLL_S)
    except ExecPsError as error:
        raise GateConfigError(f'프로세스 그룹 정리 후 생존 여부를 확인할 수 없다 — '
                              f'{error}') from error
    if ps_errors:
        raise GateConfigError(f'프로세스 그룹 정리 중 생존 여부 확인에 실패했다 — '
                              + '; '.join(str(error) for error in ps_errors))
    if signal_errors and not group_dead:
        raise GateConfigError('프로세스 그룹 신호가 거부됐고 그룹 생존 여부가 해소되지 않았다 — '
                              + '; '.join(str(error) for error in signal_errors))
    return group_dead


# ------------------------------------------------------------ 실행 엔진 본체

def as_text(value: Any) -> str:
    """TimeoutExpired 출력은 text 모드에서도 bytes로 올 수 있다 — 방어적 복원."""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value


def _tail(text: str, limit: int = TAIL_CHARS) -> str:
    return text[-limit:]


def _result(command: str, started: float, exit_code: int | None, timed_out: bool,
            stdout: str, stderr: str, survivors: bool | None,
            group_killed: bool | None) -> dict[str, Any]:
    return {'command': command, 'exit_code': exit_code, 'timed_out': timed_out,
            'duration_s': round(time.monotonic() - started, 3),
            'stdout_tail': _tail(as_text(stdout)),
            'stderr_tail': _tail(as_text(stderr)),
            'survivors': survivors, 'group_killed': group_killed}


def _run_legacy(argv: list[str], command: str, timeout: float, cwd: Path,
                started: float) -> dict[str, Any]:
    """비POSIX 폴백 — 현행 subprocess.run(timeout=) 의미 이관(직속 자식만 종료).
    process_group false로 투명 표기(C14). 명령은 호출 당시 작업 디렉터리에서
    실행된다."""
    try:
        completed = subprocess.run(argv, capture_output=True, text=True,
                                   errors='replace', timeout=timeout, cwd=str(cwd))
    except subprocess.TimeoutExpired as error:
        return _result(command, started, None, True, as_text(error.stdout),
                       as_text(error.stderr), None, None)
    except OSError as error:
        raise GateConfigError(f'--verify-cmd 실행 불가: {error}') from error
    return _result(command, started, completed.returncode, False,
                   completed.stdout, completed.stderr, None, None)


def _run_posix(argv: list[str], command: str, timeout: float, cwd: Path,
               started: float) -> dict[str, Any]:
    """posix-killpg 실행 — start_new_session으로 pgid==자식 pid 보장(P1 실측).

    TimeoutExpired 판별 규칙(리컨 P6·M6): poll()로 직속 자식 종료 여부 확인 —
    (a) 미종료 = 진성 타임아웃, (b) 이미 종료 = 파이프 잡은 orphan(타임아웃 아님 —
    exit_code 반영·survivors로 정확 분류). 양쪽 모두 stop_group 강제. 판별 경계에는
    poll() 시점 경쟁 창이 존재한다 — 보장은 orphan 시나리오 내 정확성 한정."""
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, errors='replace', start_new_session=True,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    except OSError as error:
        raise GateConfigError(f'--verify-cmd 실행 불가: {error}') from error
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        genuine_timeout = proc.poll() is None
        stdout, stderr, group_dead = stop_group(proc)
        # survivors는 (b) orphan 경로면 강제 종료됐음을 뜻하고, group_killed는
        # 그룹 소멸이 확인된 경우만 True다(소멸 미확인 undead 경로는 실측값 — 리뷰 LOW)
        survivors = (not group_dead) or (not genuine_timeout)
        return _result(command, started,
                       None if genuine_timeout else proc.returncode,
                       genuine_timeout, stdout, stderr, survivors, group_dead)
    except BaseException:
        stop_group(proc)
        raise
    survivors = detect_survivors(proc.pid)
    group_killed = False
    if survivors:
        group_killed = terminate_group(proc.pid)
    return _result(command, started, proc.returncode, False, stdout, stderr,
                   survivors, group_killed)


def split_verify_command(command: str) -> list[str]:
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise GateConfigError(f'--verify-cmd 인용 문자열이 잘못됐다: {error}') from error
    if not argv:
        raise GateConfigError('--verify-cmd가 빈 명령이다')
    return argv


def run_verify_cmd(command: str, timeout: float, cwd: Path,
                   process_group: bool) -> dict[str, Any]:
    """검증명령 1회 실행 — 타임아웃은 failed와 배타, survivors는 독립 축.

    process_group False(비POSIX 폴백)면 survivors·group_killed는 null이다."""
    argv = split_verify_command(command)
    started = time.monotonic()
    if not process_group:
        result = _run_legacy(argv, command, timeout, cwd, started)
    else:
        result = _run_posix(argv, command, timeout, cwd, started)
    return {**result, 'process_group': process_group}


# ------------------------------------------------- verify-window(전후 클린 검사)

def status_z_records(stdout: str) -> list[tuple[str, str]]:
    """porcelain -z 레코드 파서 — (XY, path) NUL 구분. R·C 레코드는 다음 필드가
    원경로다(신경로 우선). 개행 파일명 안전(r24 L4 준용)."""
    records: list[tuple[str, str]] = []
    fields = stdout.split('\0')
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 3:
            continue
        records.append((record[:2], record[3:]))
        if record[0] in ('R', 'C'):
            index += 1  # 다음 필드 = 원경로 — 폐기
    return records


def exclude_path() -> Path:
    """.git/info/exclude 절대경로 — rev-parse --git-path(cwd 상대 보정)."""
    completed = run_git('rev-parse', '--git-path', 'info/exclude')
    if completed.returncode != 0:
        raise GateConfigError('git 저장소가 아니다 — ' + git_reason(completed))
    path = Path(completed.stdout.strip())
    return path if path.is_absolute() else Path.cwd() / path


def _exclude_content(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def _fingerprint_file(path: str) -> str:
    """Hash one status-relevant path, including symlink target and executable mode."""
    target = Path(path)
    try:
        info = target.lstat()
        mode = info.st_mode
        if stat.S_ISLNK(mode):
            digest = hashlib.sha256(os.fsencode(os.readlink(target))).hexdigest()
        elif stat.S_ISREG(mode):
            hasher = hashlib.sha256()
            with target.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
        else:
            digest = f'{stat.S_IFMT(mode)}:{info.st_size}'
    except FileNotFoundError:
        return 'missing'
    except OSError as error:
        raise GateConfigError(f'검증 경로 fingerprint 실패({path}) — {error}') from error
    return f'{stat.S_IFMT(mode)}:{mode & 0o7777}:{digest}'


def _index_fingerprint() -> str:
    """Hash the active index so same-status staged rewrites are still visible."""
    completed = run_git('rev-parse', '--git-path', 'index')
    if completed.returncode != 0:
        raise GateConfigError('git index 경로 확인 실패 — ' + git_reason(completed))
    raw = Path(completed.stdout.strip())
    index = raw if raw.is_absolute() else Path.cwd() / raw
    try:
        return hashlib.sha256(index.read_bytes()).hexdigest()
    except FileNotFoundError:
        return 'missing'
    except OSError as error:
        raise GateConfigError(f'git index fingerprint 실패({index}) — {error}') from error


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(path, pattern) or
               fnmatchcase(Path(path).name, pattern) for pattern in patterns)


def window_snapshot(patterns: tuple[str, ...]) -> dict[str, Any]:
    """HEAD/status/index/exclude plus content hashes for dirty tracked and test inputs.

    Git status alone cannot detect rewriting an already-dirty path because its XY state
    stays the same. Hash all status-dirty tracked paths and status-visible untracked
    verification inputs; regular clean tracked files are covered by status transitions.
    """
    status = run_git('status', '--porcelain', '-z', '-uall')
    if status.returncode != 0:
        raise GateConfigError('git status 스냅샷 실패 — ' + git_reason(status))
    records = status_z_records(status.stdout)
    tracked_paths = sorted({path for xy, path in records if xy != '??'})
    untracked_inputs = sorted({path for xy, path in records
                               if xy == '??' and _matches(path, patterns)})
    exclude = exclude_path()
    return {'head': git_ok('rev-parse', 'HEAD'),
            'porcelain': status.stdout,
            'tracked_fingerprints': {path: _fingerprint_file(path)
                                     for path in tracked_paths},
            'untracked_fingerprints': {path: _fingerprint_file(path)
                                       for path in untracked_inputs},
            'index_fingerprint': _index_fingerprint(),
            'exclude': _exclude_content(exclude)}


def window_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Execution-window delta, including content edits hidden by an unchanged XY state."""
    before_records = status_z_records(before['porcelain'])
    after_records = status_z_records(after['porcelain'])
    before_tracked = {(xy, path) for xy, path in before_records if xy != '??'}
    after_tracked = {(xy, path) for xy, path in after_records if xy != '??'}
    before_untracked = {path for xy, path in before_records if xy == '??'}
    after_untracked = {path for xy, path in after_records if xy == '??'}
    tracked_changed = {f'{xy} {path}' for xy, path
                       in (after_tracked - before_tracked)
                       | (before_tracked - after_tracked)}
    for path in before['tracked_fingerprints'].keys() | after['tracked_fingerprints'].keys():
        if before['tracked_fingerprints'].get(path) != after['tracked_fingerprints'].get(path):
            tracked_changed.add(f'content:{path}')
    untracked_changed = sorted(
        path for path in before['untracked_fingerprints'].keys()
        | after['untracked_fingerprints'].keys()
        if before['untracked_fingerprints'].get(path)
        != after['untracked_fingerprints'].get(path))
    return {'head_moved': before['head'] != after['head'],
            'tracked_changed': sorted(tracked_changed),
            'untracked_new': sorted(after_untracked - before_untracked),
            'untracked_gone': sorted(before_untracked - after_untracked),
            'untracked_changed': untracked_changed,
            'index_changed': before['index_fingerprint'] != after['index_fingerprint'],
            'exclude_changed': before['exclude'] != after['exclude']}


# Fresh checkouts are deliberately unsupported. Safe ownership and cleanup of an
# isolated worktree require a separate design; no fixed path or cleanup is allowed.
