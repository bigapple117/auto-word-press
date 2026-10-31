# AGENTS 역할 정의

<!-- BEGIN AI-TEAM -->
## AI 팀 체계: Claude 총괄·작성 / Gemini 조사 / Codex 검증

- 지휘 체계: 사용자 > Claude(Claude Code) > Codex, Gemini(Antigravity). 작업 배분, 검토자 지정, 배포 승인 등 최종 결정은 Claude가 내리고 Codex와 Gemini는 Claude의 지시를 따릅니다.
- 기본 역할:
  * Claude: 계획, 코드 수정, 최종 판단. 기본 코드 작성자는 Claude입니다.
  * Gemini: 조사 전담, 읽기 전용. 관련 파일 찾기, 기존 구조 요약, 라이브러리·문서 조사를 하고 파일 경로·핵심 줄을 담은 요약으로 보고합니다. 파일 수정·커밋은 하지 않습니다.
  * Codex: 검증 전담, 기본 읽기 전용. 기능 단위가 끝나면 리뷰, 테스트 실행, 버그 원인 분석을 하고 결과를 보고합니다.
- 예외 1 (단순 반복 수정): Claude가 `[지시: Claude]`로 명시적으로 수정을 맡긴 단순 반복 작업(이름 일괄 변경, 테스트 코드 작성 등)은 Codex가 지시 범위 안에서 수정합니다. 커밋·푸시·배포는 하지 않고 변경 내용을 보고하며, Claude가 확인한 뒤 커밋합니다.
- 예외 2 (Claude 사용량 한도 소진): 사용자가 Codex에서 직접 수정을 지시하면 Codex가 작성자로 이어받습니다. HANDOFF.md와 Git 상태로 진행 상황을 파악해 이어서 작업하고, 일반 규칙(커밋·푸시 등)을 따르며, HANDOFF.md에 "작업자: Codex (Claude 한도로 대행)"와 변경·검증·남은 작업을 기록해 Claude가 복귀하면 이어받게 합니다. Claude와 Codex를 모두 쓸 수 없어 사용자가 Antigravity에서 직접 수정을 지시한 경우에만 Gemini가 같은 방식으로 대행합니다.
- `[지시: Claude]`로 시작하는 위임 작업은 지시된 범위만 수행하고, 범위 밖 변경·배포·서비스 재시작은 하지 않으며, 결과(변경 파일, 실제 검증, 남은 문제)를 보고합니다. Codex와 Gemini는 서로에게 작업을 중계·위임하지 않습니다.
- 한 프로젝트에서는 한 번에 한 AI만 코드를 수정합니다. 검토 중에는 대상 코드 수정을 멈춥니다. Claude의 규칙 파일(`~/.claude/`, `CLAUDE.md`)은 수정하지 않습니다.
- 로그인·권한·결제/금액 계산·DB 구조/데이터 이전·동기화·삭제·보안 관련 변경은 운영 반영 전에 작성자와 다른 AI의 독립 검토를 받습니다. 기본 검토자는 Codex(`python3 /home/ubuntu/.codex/scripts/codex-review.py --project "프로젝트 절대 경로"`)이고, Codex가 작성한 변경은 Claude가 검토합니다.
- 검토 결과는 PASS / CHANGES_REQUESTED / BLOCKED와 근거·파일:줄·검증/미검증 항목으로 REVIEW.md에 남깁니다. 호출 성공은 승인과 다르고, 과거 PASS를 새 변경의 승인으로 쓰지 않습니다. 호출 실패는 `blocked`로 보고하며 필수 검토를 생략해 배포하지 않습니다.
- 시작 시 AGENTS.md, PROJECT.md, HANDOFF.md, Git 상태를 읽습니다. HANDOFF.md에 지시자·작업자·검토자, 변경 이유·파일·커밋·실제 검증·남은 작업을 기록합니다. 과거 기록은 소급 수정하지 않습니다.
- 공통 역할 문서: `/home/ubuntu/.gemini/COLLABORATION.md`.
<!-- END AI-TEAM -->
