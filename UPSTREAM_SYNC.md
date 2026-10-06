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
git merge remove-jev                    # SKILL.md 는 거의 항상 충돌한다 — 아래 "SKILL.md 분할 구조" 순서로 푼다
git push origin klic-overlay
```

### SKILL.md 분할 구조 (klic-overlay 전용)

klic-overlay 의 `SKILL.md` 는 **핵심본**(약 17KB)이고, 업스트림 본문의 긴 절은 `reference/code_tiers.md`·`state_handoff.md`·`harness_install.md` 로 **원문 그대로** 옮겨져 있다. 이 세 파일은 `scripts/split_skill.py` 가 만든 것이라 손으로 고치지 않는다. 그래서 `klic-overlay` 에 `remove-jev` 를 병합할 때는:

```bash
git checkout --ours SKILL.md                                   # 핵심본은 우리 것을 유지
git diff <병합 전 remove-jev> remove-jev -- SKILL.md           # 업스트림이 바꾼 문장을 읽는다
git show remove-jev:SKILL.md | python3 scripts/split_skill.py - --out reference   # 참고 파일 재생성
git add SKILL.md reference/
```

- `split_skill.py` 가 "새 절이라 분류할 수 없음"으로 멈추면 업스트림이 `## ` 절을 새로 만든 것이다. 그 절을 참고 파일로 보낼지(`BUCKETS`) 핵심본에 둘지(`KEEP`) 정하고 다시 실행한다.
- 업스트림이 바꾼 문장이 핵심본에 **요약**으로 들어 있는 부분(When to Use, §1 요약, §2 역할표·단계표, §3 핵심 제약, Output Contract)이면 핵심본도 손으로 맞춘다.
- 끝나면 `split_skill.py ... --check` 가 "모두 일치"여야 한다.

각 병합이 끝날 때마다 아래 3번의 시험을 돌린다. 충돌이 나면 `git merge --abort`로 되돌릴 수 있다(푸시 전에만).

## 3. 충돌이 나기 쉬운 곳 (overlay 접점)

| 파일 | 우리가 바꾼 부분 | 충돌 시 원칙 |
|---|---|---|
| `SKILL.md` | 파일 전체가 핵심본(분할 구조) | 우리 것을 유지하고 업스트림 변경은 위 "SKILL.md 분할 구조" 순서로 반영한다 |
| `reference/code_tiers.md`·`state_handoff.md`·`harness_install.md` | `split_skill.py` 생성물 | 손으로 풀지 말고 재생성한다 |
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
python3 scripts/test_split_skill.py
git show remove-jev:SKILL.md | python3 scripts/split_skill.py - --out reference --check   # 모두 일치
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
- `SKILL.md`(핵심본)는 2026-10-06 분할 때부터 저장소 파일과 설치본이 같다. 복사 전 `diff`로 설치본에만 있는 줄이 없는지 확인하고 덮어쓴다.
- 새 스크립트·`reference/`·`ext_allowlist.json`은 파일 단위로 복사한다. 설치본에는 시험 스크립트·`TESTS.md`·`books/` 를 두지 않는다.
- `ext_allowlist.local.json`(허용 폴더)은 설치본에만 있고 저장소에는 없다 — 덮어쓰거나 지우지 않는다.

## 6. 문제가 생기면

| 상황 | 대처 |
|---|---|
| 병합 중 충돌이 복잡하다 | `git merge --abort` 후 변경 범위를 줄여 다시 시도 |
| 푸시한 뒤 문제를 발견했다 | 이력을 지우지 말고 되돌리는 커밋(`git revert`)을 올린다 |
| 업스트림이 구조를 크게 바꿨다 | 이 문서의 3번 표를 먼저 갱신하고 접점을 다시 찾는다 |
