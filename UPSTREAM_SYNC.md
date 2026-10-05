# 업스트림 갱신 절차

원본(`smshin86/Effort-Router`)이 바뀌었을 때 이 포크의 개인 overlay를 안전하게 다시 얹는 순서다.

> **상태**: 이 절차는 아직 실제 업스트림 변경으로 실행해 본 적이 없다. 처음 쓸 때 명령 하나씩 결과를 확인하고, 어긋나면 이 문서를 고친다.

## 브랜치 구조

| 브랜치 | 내용 | 비고 |
|---|---|---|
| `main` | 업스트림과 같은 상태 | 직접 수정하지 않는다 |
| `remove-jev` | `main` + jev(외부 API 호출) 제거 | |
| `klic-overlay` | `remove-jev` + 문서 티어·절약 모드·외부 모델 게이트 등 | 실제 사용 대상 |

원격: `origin` = 내 포크(`0x-mw/Effort-Router`), `upstream` = 원본(`smshin86/Effort-Router`).
세 브랜치는 이미 원격에 올라가 있으므로 **`rebase`로 이력을 다시 쓰지 말고 `merge`로 쌓는다.** 강제 푸시(`--force`)는 쓰지 않는다.

## 1. 가져오기 전 점검

```bash
git status --short                      # 비어 있어야 한다
git remote -v                           # origin / upstream 둘 다 보여야 한다
git fetch upstream
git log --oneline main..upstream/main   # 무엇이 바뀌었는지
git diff main upstream/main --stat
```

변경 내용을 **먼저 읽는다.** 특히 아래가 새로 생겼는지 본다 — 있으면 `klic-skill-security` 점검 후에 진행한다.

```bash
git diff main upstream/main | grep -nE '^\+.*(http|requests|urllib|subprocess|os\.environ|TYPESAFE|hooks|settings\.json)'
```

## 2. 순서대로 병합

```bash
git switch main
git merge --ff-only upstream/main       # 실패하면 main 이 변형된 것 — 중단하고 원인 확인
git push origin main

git switch remove-jev
git merge main                          # jev 가 다시 들어왔거나 수정됐으면 계속 제거한다
git push origin remove-jev

git switch klic-overlay
git merge remove-jev
git push origin klic-overlay
```

각 병합이 끝날 때마다 아래 3번의 시험을 돌린다. 충돌이 나면 `git merge --abort`로 되돌릴 수 있다(푸시 전에만).

## 3. 충돌이 나기 쉬운 곳 (overlay 접점)

| 파일 | 우리가 바꾼 부분 | 충돌 시 원칙 |
|---|---|---|
| `SKILL.md` 머리말 | `description` 전체 | 우리 문구를 유지한다 |
| `SKILL.md` | `## KLIC 개인 overlay` 절 전체(§1과 §2 사이) | 통째로 우리 것을 유지한다 |
| `SKILL.md` | §3의 `model override 금지` 항목(절약 모드 예외 한 문장) | 업스트림 문구 + 우리 예외를 합친다 |
| `SKILL.md` | `Output Contract`의 티어 목록·절약 모드·외부 모델 줄 | 업스트림 항목 + 우리 줄을 합친다 |
| `agents/review-pr-high.md`, `review-pr-xhigh.md` | `tools:` 줄에서 `WebFetch`·`WebSearch` 제거 | 우리 쪽 유지 |
| `scripts/ext_dispatch.py`, `ext_allowlist*.json`, `reference/`, `scripts/tier_eval*`, `usage_report*`, `check_tier_table*` | 우리가 추가한 파일 | 업스트림에 같은 이름이 생기면 중단하고 확인 |
| `README.md` | `scripts/`·`reference/` 행, 설치 명령, 허용 폴더 안내 | 업스트림 문구 + 우리 항목을 합친다 |
| `.gitignore` | `ext_allowlist.local.json`, `tier_eval_out/` | 둘 다 유지 |

## 4. 병합 후 검증

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 scripts/test_ext_dispatch.py
python3 scripts/test_tier_eval.py
python3 scripts/test_usage_report.py
python3 scripts/test_check_tier_table.py
python3 scripts/tier_eval.py --dry-run
python3 scripts/check_tier_table.py
git grep -niE 'jev|typesafe' -- . ':!books' ':!UPSTREAM_SYNC.md' ':!SKILL.md'   # 결과가 없어야 한다
```

규칙이 바뀌었을 가능성이 있으면 판정 시험도 돌린다. **실행하면 Claude 한도를 쓴다**(사례 15건 = 새 프로세스 15회).

```bash
python3 scripts/tier_eval.py --workdir <작업 폴더> --out /tmp/eval_after
```

## 5. 설치본에 반영

- 설치본은 계정 사이에 공유될 수 있다(링크 구조). 복사 전에 현재 설치본을 백업한다.
- `SKILL.md`는 저장소 파일과 설치본이 일부 다르다(설치 환경 메모 등). **통째로 덮어쓰지 말고** `diff`로 확인해 해당 변경만 반영한다.
- 새 스크립트·`reference/`·`ext_allowlist.json`은 파일 단위로 복사한다.
- `ext_allowlist.local.json`(허용 폴더)은 설치본에만 있고 저장소에는 없다 — 덮어쓰거나 지우지 않는다.

## 6. 문제가 생기면

| 상황 | 대처 |
|---|---|
| 병합 중 충돌이 복잡하다 | `git merge --abort` 후 변경 범위를 줄여 다시 시도 |
| 푸시한 뒤 문제를 발견했다 | 이력을 지우지 말고 되돌리는 커밋(`git revert`)을 올린다 |
| 업스트림이 구조를 크게 바꿨다 | 이 문서의 3번 표를 먼저 갱신하고 접점을 다시 찾는다 |
