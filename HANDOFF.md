# HANDOFF

- 현재 상태: 기획 및 아키텍처 설계 완료 (`PLAN.md`), Claude 구현 착수 대기
- 지시자: 사용자
- 이전 작업자 (기획/설계): Gemini (Codex 자문 2회 완료)
- 차기 메인 작업자 (구현): Claude (Claude Code)
- 검토자: Codex (기능 단위 완료 후)

## 작업 히스토리 요약
1. 1단계 기획: 구글 시트 주제 입력 ➡️ AI 제목/본문(실용 위젯) 생성 ➡️ 워드프레스 REST API 자동 발행 ➡️ 텔레그램 승인/알림 파이프라인 수립.
2. 2단계 기획 확장: 무료 스톡 API(Pexels, Pixabay) 문단별 이미지/영상 자동 삽입 + AI 성우(Edge-TTS) + 자막 싱크 + FFmpeg 숏폼(1080x1920) 자동 렌더링 + 웹 대시보드(진행률 콘솔) 통합 기획 확정.
3. 기획 상세 문서: `/home/ubuntu/auto-word-press/PLAN.md`에 아키텍처, 디렉터리 구조, 열 구성, 4단계 렌더링 파이프라인, 테스트 계획을 완벽하게 작성 완료.

## Claude가 이어받을 구현 작업 (Next Steps)
1. Python 가상환경(`.venv`) 및 의존성 구성 (`requirements.txt`: `requests`, `gspread`, `google-genai`, `openai`, `edge-tts`, `pillow`, `pydantic`, `fastapi`, `uvicorn` 등)
2. ARM64 호환 FFmpeg 및 한글 폰트(`assets/fonts/`) 준비
3. 모듈별 구현 (`PLAN.md` 5절 파일 구조 준수):
   - `templates/prompt_template.py`: 블로그 HTML + 쇼츠 대본 마스터 JSON 프롬프트
   - `src/stock_media.py`: Pexels / Pixabay API 검색 및 다운로드
   - `src/tts_engine.py`: Edge-TTS 음성 합성 및 자막 타임스탬프(.ass) 생성
   - `src/video_renderer.py`: FFmpeg 필터그래프 기반 세로형 쇼츠 합성
   - `src/wp_client.py`: WordPress REST API 포스팅 및 미디어 업로드
   - `src/sheet_manager.py`: Google Sheets 연동
   - `src/web_ui.py`: 실시간 4단계 콘솔 UI (두 번째 레퍼런스 스타일)
   - `src/orchestrator.py`: 단일/배치 오케스트레이션
4. 단위 테스트 및 통합 드라이런 검증 (`tests/`)

## 2026-10-31 새 운영 서버 연결 (지시: 사용자 / 작업: Claude, PC 쪽 Claude 세션 / 검토: 없음, 코드 변경 없음)
- 결정: 워드프레스와 자동 생성 프로그램을 모두 새 오라클 서버에 둠 (사용자 선택 3번)
- 새 서버: Ubuntu 24.04 ARM64, CPU 1개, 메모리 5.8GiB, 디스크 41G 남음. 접속 주소는 이 서버 `~/.ssh/known_hosts`와 사용자에게 확인
- 이미 설치됨: nginx, PHP 8.3-fpm, MariaDB, 워드프레스 7.1.4(`/var/www/html`), python3, git, Node, Claude Code
- 설치 안 됨: ffmpeg, cloudflared, SSL(https), 도메인
- 워드프레스 웹 설치는 사용자가 직접 마침 (비밀번호는 사용자가 보관)
- 이 서버 공개키를 새 서버에 등록함 (사용자가 PC 세션에서 직접 승인)
- 실제 검증: 이 서버에서 키 파일 없이 SSH 접속 성공, `install.php`가 "이미 설치됨"으로 응답
- 남은 일: ffmpeg와 한글 폰트 설치, 도메인과 https 적용, CPU 증설 검토(무료 등급 여유분), PLAN.md 순서대로 구현

## 2026-11-01 운영 서버 접속 별명 등록 (지시: 사용자 / 작업: Claude / 검토: 없음, 코드 변경 없음)
- 운영 서버(워드프레스): 공인 IP 168.110.20.97, 사설 IP 10.0.0.118, 호스트 이름 instance-20261031-0906
- 이 서버 `~/.ssh/config`에 별명 `wp-server` 등록 → `ssh wp-server`로 접속
- 실제 검증: 키 접속 성공, 서버 안에서 워드프레스 첫 화면 응답 200, ffmpeg 아직 없음
- 참고: 사설 IP로도 연결은 되나 호스트 키 미등록 상태라 공인 IP 사용
