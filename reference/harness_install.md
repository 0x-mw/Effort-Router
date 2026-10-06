# reference — harness_install

Codex·ChatGPT·Gemini 등 다른 하니스에 설치하거나 대응 관계를 확인할 때, 실패 원장 규칙을 다룰 때 읽는다.

> 이 파일은 업스트림 SKILL.md 본문을 **원문 그대로 옮긴 것**이다(`scripts/split_skill.py` 가 생성 — 손으로 고치지 않는다).
> 본문의 `§N` 표기는 reference/ 안의 같은 번호 절을 가리킨다. 이 파일과 SKILL.md 핵심본이 충돌하면 **핵심본이 우선**한다 (예: 절약 모드의 model override 예외, 문서 티어 D1~D3, 외부 모델 게이트).

## 설치·업데이트 요청 계약

Codex 또는 ChatGPT 데스크톱 앱에 이 Skill을 설치·업데이트해 달라는 요청에는 파일 복사만 안내하지 않는다. 아래 5가지를 모두 제시해야 설치 안내 완료다.

1. Skill을 `~/.codex/skills/effort-router/`에 설치
2. custom-agent TOML을 `~/.codex/agents/`에 설치
3. `~/.codex/config.toml`에 신규 설치 기본 `gpt-6.1-sol / medium`과 custom-agent 활성 설정을 **병합**
4. `~/.codex/AGENTS.md`에 전역 발동·승격 규칙과 실패 원장 규칙을 **병합**
5. Codex/ChatGPT 앱 재시작 후 설치 검증 실행

기존 `config.toml`과 `AGENTS.md` 전체를 덮어쓰지 않는다. 필요한 키·단편만 병합하며, 동일 TOML table을 중복 생성하지 않는다. Skill 파일 존재와 전역 활성화는 별도 상태로 구분해 보고한다. 정확한 설정 단편과 검증 명령은 `platforms/codex.md`를 따른다.

## 가이드 파일 = 실패 원장

주요 agent guide file은 Codex의 `AGENTS.md`, Claude Code의 `CLAUDE.md`, Cursor의 `.cursorrules`다. 이 파일에 새로 누적하는 예방 규칙은 **관측되거나 재현된 과거 실패 1건을 영구 예방 장치 1줄로 변환한 것**이어야 한다.

- 한 실패에는 한 규칙만 추가한다. 원인·트리거와 필수/금지 행동이 한 줄에서 실행 가능해야 한다.
- 추가 전에 기존 규칙을 검색한다. 같은 실패를 다루면 새 줄을 만들지 않고 기존 규칙을 더 정확하게 고친다.
- 규칙의 폐기·축소도 관측 근거로 한다 — 트리거 실패가 재관측되지 않는 규칙은 회차에서 폐기 후보로 표기한다(원장은 단조 증가하지 않는다).
- 추측성 예방책, 일반론, 사건 서사는 넣지 않는다. 규칙은 다음 작업에서 준수 여부를 판정할 수 있어야 한다.
- 모든 저장소에 적용되는 실패만 전역 guide file에 둔다. 프로젝트 고유 실패는 가장 가까운 프로젝트 guide file에 둔다.
- 설치·업데이트 요청에서 기존 파일을 덮어쓰지 않고 해당 하니스의 guide file에 실패 원장 단편을 병합한다.

## 6. 환경 매핑

Codex와 ChatGPT는 §2의 OpenAI 모델 정책을 직접 적용한다. 다른 하니스는 티어 판정(§1)·증거 계약·Output Contract만 유지하고 고유 모델 체계로 치환한다.

| 개념 | Claude Code (기준) | Codex CLI | Qwen Code | Gemini CLI |
|------|--------------------|-----------|-----------|------------|
| 서브에이전트 | `~/.claude/agents/` + Agent 스폰 | 멀티에이전트 도구(`spawn_agent` 등, 기본 on)·커스텀 에이전트 TOML(`~/.codex/agents/`·`.codex/agents/`) | `/fork` 배경 에이전트(전체 대화 상속) — 역할별 스폰 미확인 | 빌트인 서브에이전트(`@이름` 지정·자동 위임) + `settings.json` `agents.overrides` |
| effort | 에이전트 파일 frontmatter 고정 | `model_reasoning_effort`(low~max, 지원 범위는 모델 의존)·스폰 기본값 `agents.default_subagent_reasoning_effort` | settings `model.*` — effort 상당 키 미확인 | thinking budget(생성 설정) — effort와 직접 대응 없음 |
| 모델 지정 | 호출 시 `Agent(model:)` 덮어쓰기 | 스폰 시 명시(없으면 `agents.default_subagent_model`) | `settings.json`(프로젝트 > 사용자) | `agents.overrides.<에이전트>.modelConfig.model` |
| 팬아웃(§2) | `plan-adversary-xhigh` 병렬 | 독립된 하위 작업이 있을 때 동명 custom agent를 스폰하며 L=3렌즈, XL=5렌즈 | 미확인 | 위임 가능하나 렌즈 팬아웃 계약 없음 → 미적용 |
| 상태·인계(§5) | state.json + 증거번들(스폰 간 인계) | 파일 계약은 하니스 무관 — 수동 운반 시에만 성립 | 좌동 | 좌동 |
| 컨텍스트 등재 | CLAUDE.md·스킬 | AGENTS.md(`~/.codex/` → 리포 → 하위 디렉터리) + 로컬 Skill | QWEN.md(+AGENTS.md 판독) | GEMINI.md(글로벌 → 워크스페이스 → JIT) |

- **ChatGPT 데스크톱 앱 Codex 화면**: Codex CLI와 같은 로컬 Skill·`config.toml`·custom agents를 사용한다.
- **ChatGPT Work**: Skill은 사용할 수 있으나 로컬 Codex custom-agent TOML을 전제로 하지 않는다. 에디터의 모델·reasoning control에서 §2 조합을 선택하고, 호스팅 subagent는 실제 독립 병렬 작업이 있을 때만 요청한다.

- **GLM Coding Plan(Z.ai)**: 하니스가 아니라 Claude Code의 백엔드 교체다 — 적용 대상에 Claude Code가 공식 포함됨. 슬롯 실체가 프록시 매핑을 따르고 effort 강등(§3 실측)이 발생할 수 있다. 이원 모델 정책으로 모든 플랜이 GLM-5.3과 GLM-5.3-Flash를 함께 지원한다 — 치환 기준과 비전(시각 이해 MCP 번들)은 `platforms/glm.md`를 따른다(zcode.md는 동결 어댑터 — 사용 요청 시 본문에서 재생성). 심층(xhigh급) 스폰은 백엔드 동시성 상한 내에서만 병렬한다(GLM-5.3 상한 1 — 팬아웃은 렌즈 직렬화·경량 렌즈 병렬로 치환, `platforms/glm.md`) — 사용자 명시 발화 시 심층 병렬 상향(무지정 2).
- **Claude Code 10세션 토폴로지**: 세션 1 두뇌(메인)·세션 2 감시 Ops(`ops-supervisor`)·세션 3–10 작업 슬롯(심층 1(사용자 명시 상향 예외) + 경량 7)의 자율 병렬 운용 형태다 — 슬롯 배정·GLM 동시성 치환·두뇌 프로토콜은 `platforms/claude.md`를 따른다.
- 표의 '미확인'은 공식 문서에서 확인하지 못한 항목이다 — 확인 전까지 단정하지 않는다.
- 하니스별 삽입 단편과 설치 절차는 동봉 `platforms/`에 둔다 — 본문이 우선, 어댑터는 파생이다. 실행·실측 기반 하니스(codex·glm·claude·README)는 유지 관리하고, 미실행 하니스(qwen·gemini·grok·zcode·chat-app) 어댑터는 동결한다 — 갱신 대상에서 제외, 사용 요청 시 본문에서 재생성한다(동결 근거: 실측 통과 0건·파생 동기화 실패 1건 r11).
