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
