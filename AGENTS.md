# AGENTS.md

코딩 작업 착수 전 설치된 `effort-router`를 사용한다. Output Contract로 티어·단계·모델·effort를 먼저 밝힌다. 일반 계획은 GPT-6.1-Sol medium, L/XL 계획과 계획 적대검토·모든 PR 리뷰는 Sol xhigh, 보안 감사와 예외적 고위험 판단은 Plus에서 Astra medium·Pro에서 Astra high, 실무 실행은 Luna max를 사용한다. 데이터 손실·불가역 변경의 최종 판단 또는 동일 접근 2회 실패·불안정 재현·반복 테스트 실패 시 메인 세션을 UI 또는 `/model`에서 요금제별 Astra로 전환한다. role 모델을 override하지 않는다. 해결안이 확정되면 계획은 해당 티어의 Sol role로, 실행은 Luna max로 재개한다. 역할 호출 전 configure_codex_plan.py로 요금제를 확인하고 불일치 시 --apply 적용 및 Codex 재시작 후 진행한다. 높은 effort만으로 멀티에이전트를 자동 사용하지 않는다.

## 실패 기반 예방 규칙

- When checking whether verification changed a workspace, compare file-content and index fingerprints as well as Git status; never treat an unchanged status of an already-dirty file as unchanged content.
- When a test script imports a sibling module by bare name, invoke the script directly from the repository root (for example, `python3 scripts/test_plan_routing.py`) or set the script directory on `PYTHONPATH`; never assume `python -m scripts.<module>` adds that directory.
