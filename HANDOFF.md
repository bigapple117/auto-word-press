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

## 2026-11-01 작업 장소를 운영 서버로 이전 (지시: 사용자 / 작업: Claude / 검토: 없음, 코드 변경 없음)
- 결정: 앞으로 auto-word-press 개발·실행은 운영 서버(`wp-server`)의 `~/auto-word-press`에서 그 서버의 Claude로 진행
- 이 서버(ubuntu@instance-20260913-2037)의 `~/auto-word-press`는 보관용. 여기서는 코드를 고치지 않음 (두 곳 동시 수정 방지)
- 운영 서버에 서버 맞춤 공통 규칙 `~/.claude/CLAUDE.md` 작성 (원본은 이 서버 `~/.claude/CLAUDE.md`)
- 운영 서버에 `~/.local/bin/codex`, `~/.local/bin/gemini` 있음 → 위임 가능
- 남은 일: 운영 서버에서 `git pull` 후 `scripts/setup_media_tools.sh` 실행(FFmpeg·한글 폰트), 이후 PLAN.md 순서대로 구현

## 2026-11-01 FFmpeg·한글 폰트 설치 확인 (지시: 사용자 / 작업: Claude, 운영 서버 / 검토: 없음, 코드 변경 없음)
- `scripts/setup_media_tools.sh` 실행: 오늘 02:41에 이미 받아 둔 상태라 다시 받지 않고 넘어감 (정상 종료)
- 설치된 것: `tools/ffmpeg/{ffmpeg,ffprobe}` 7.0.2 정적 빌드(ARM64), `assets/fonts/NotoSansCJKkr-{Regular,Bold}.otf` (Git 제외 대상)
- 실제 검증: libx264·aac 인코더와 `ass`/`subtitles` 필터 있음. 1080x1920 2초 영상에 한글 .ass 자막을 입혀 렌더링 성공(약 1.5초 걸림), 캡처 화면에서 한글이 정상 표시됨
- 주의: 이 빌드에는 `drawtext` 필터가 없음 → 화면 글자는 모두 .ass 자막(libass)으로 넣어야 함. 자막 스타일 글꼴 이름은 `Noto Sans CJK KR`, ffmpeg에 `ass=파일:fontsdir=assets/fonts` 지정
- 남은 일: PLAN.md 순서대로 구현 (.venv와 requirements.txt부터)

## 2026-11-01 운영 서버 GitHub 연결 (지시: 사용자 / 작업: Claude / 검토: 없음, 코드 변경 없음)
- 운영 서버에 GitHub 인증 수단이 없어 push 실패 → 사용자 선택 1번(저장소 전용 Deploy key)
- 키: `~/.ssh/github_auto_word_press`, `~/.ssh/config` 별명 `github-awp`, GitHub 호스트 키 지문 공식값과 대조 후 `known_hosts` 등록
- 사용자가 GitHub 저장소 Deploy keys에 쓰기 권한으로 공개키 등록, 승인 후 origin을 `git@github-awp:bigapple117/auto-word-press.git`로 변경
- 저장소 전용 git 작성자 설정: bigapple117 (이전 커밋과 동일)
- 실제 검증: `38a932e` push 성공, `main...origin/main` 일치
- 남은 일: PLAN.md 순서대로 구현 (.venv와 requirements.txt부터)

## 2026-11-02 도메인 jalcheck.com + https 적용 (지시: 사용자 / 작업: Claude / 검토: Codex 자문(순서 변경 권고 반영))
- 결정: 블로그 주제 "생활 돈 정보(지원금·제도·절약)", 도메인 jalcheck.com (Cloudflare Registrar, 연 $11.17 자동 연장, Free 요금제)
- DNS(Cloudflare, 프록시 켬): A `@` → 168.110.20.97, CNAME `www` → jalcheck.com
- 인증서: Cloudflare Origin CA(ECDSA, 2041-10-29 만료, jalcheck.com·*.jalcheck.com). 키는 서버에서 생성 `/etc/ssl/private/jalcheck.com.key`(root 600, 서버 밖 반출 없음), 인증서 `/etc/ssl/certs/jalcheck.com.pem`
- Cloudflare 설정(사용자): SSL/TLS Full (strict), Always Use HTTPS 켬
- nginx: 80 → `https://jalcheck.com` 301, 443 http2, www → 루트 도메인 301. 사본 `deploy/nginx/default.conf`
- 워드프레스: siteurl/home `http://168.110.20.97` → `https://jalcheck.com`, 기본 페이지 2개(ID 2 예제, 3 개인정보 처리방침 초안) 본문 IP 링크 치환. wp-config 주소 상수·객체 캐시 없음 확인
- 방화벽: 서버 iptables·오라클 Security List 모두 443 이미 허용(변경 없음)
- 백업: `~/backups/2026-11-02-https/` (nginx 원본, iptables 규칙, DB 전체 덤프)
- 되돌리기: 백업 nginx 설정 복원+reload, DB siteurl/home 원복(또는 덤프 복원), Cloudflare SSL 모드 원복. 301은 브라우저에 캐시될 수 있음
- 실제 검증: https 200, http/www/IP 접속 모두 https://jalcheck.com 으로 301, wp-admin 로그인 사용자 확인, 페이지 내 링크 전부 https://jalcheck.com, wp-json 200
- 남은 일: 개인정보 처리방침 실제 내용 작성(애드센스 승인 필요), 예제 페이지·샘플 글 정리, (선택) nginx에 Cloudflare 실제 방문자 IP 복원 설정, PLAN.md 구현(.venv부터)

## 2026-11-02 파이썬 실행 환경 구성 (지시: 사용자 / 작업: Claude / 검토: 없음, 의존성 구성만)
- 서버에 pip·python3-venv 패키지가 없어 sudo 설치 대신 `python3 -m venv --without-pip .venv` + 공식 get-pip.py로 프로젝트 안에만 pip 설치
- `requirements.txt`(버전 고정): requests, python-dotenv, pydantic, google-genai, gspread, edge-tts, pillow, fastapi, uvicorn / `requirements-dev.txt`: pytest
- openai는 유료 API라 이번엔 뺌. AI 글 생성 모듈 만들 때 Gemini 무료 등급으로 충분한지 보고 필요하면 추가
- 텔레그램은 별도 라이브러리 없이 requests로 호출 예정, 작업 상태 DB는 내장 sqlite3 사용
- `.gitignore`에 `.venv/`, `.env`, `__pycache__/` 추가
- 실제 검증: `pip check` 충돌 없음, 전체 import 성공, edge-tts 한국어 음성(ko-KR-SunHiNeural) 합성 성공(5.04초 mp3 + 문장별 시작·끝 시간 자막 생성)
- 참고: 한국어 성우 3종 사용 가능(SunHi 여, InJoon 남, HyunsuMultilingual 남)
- 남은 일: PLAN.md 5절 순서로 src/config.py, .env.example, src/db.py부터 구현

## 2026-11-02 설정 로더·작업 장부 구현 (지시: 사용자 / 작업: Claude / 검토: Codex 3회, 최종 PASS — REVIEW.md)
- 추가: `src/config.py`(.env 읽기, 기능별 require(), 비밀값 repr 가림), `.env.example`, `src/db.py`(SQLite 작업 장부), `tests/`(35개), `pytest.ini`, `.gitignore`에 `data/`
- db.py 핵심: 시트 행 등 같은 출처 중복 등록 방지(source+source_ref 고유), 상태 흐름 강제(queued→generating→tts→media→rendering→awaiting_approval→publishing→published, failed/canceled), 단계별 체크포인트 저장, 글 번호 1회 기록(record_post)·글 번호 중복 연결 금지, 발행 선점 begin_publishing(동시 승인·취소 경쟁 차단), 재시작 복구 recover_interrupted, 구조 버전(user_version=1) 불일치 DB 거부
- 고친 버그: `.env`에 빈 값(`TTS_VOICE=`)이면 기본값 대신 빈 값이 들어가던 문제
- 실제 검증: `.venv/bin/python -m pytest -q` 35 passed (동시 4~5개 연결 선점 시험 포함)
- Codex 자동 검토 스크립트(codex-review.py)는 이 서버에서 bwrap 오류로 BLOCKED → codex-consult.py에 코드 직접 전달 방식으로 대체
- 남은 일: wp_client에서 글에 작업 번호 표시 + 생성 전 조회(중복 초안 방지, 필수), 실행기 시작 시 recover_interrupted 1회 호출, GEMINI_MODEL 기본값(gemini-2.5-flash) 실제 사용 가능 모델로 확인, 다음 구현 templates/prompt_template.py·src/ai_generator.py

## 2026-11-02 Gemini 키 등록·모델 확인 (지시: 사용자 / 작업: Claude / 검토: 없음)
- `scripts/set_secret.sh` 추가: .env 값을 화면에 안 보이게 입력(.env.example에 있는 이름만, 파일 권한 600)
- `.env` 생성(600, Git 제외), GEMINI_API_KEY 저장. 사용자가 채팅으로 키 전달(터미널 사용 어려움) → 결제 미연결 무료 키로 안내, 필요 시 AI Studio에서 교체
- gemini-2.5-flash는 "새 사용자에게 제공 안 함"(404) → 기본 모델을 gemini-3.8-flash로 변경(config, .env.example, .env)
- 실제 검증: 키로 모델 목록 조회 성공, gemini-3.8-flash 한국어 응답 성공(약 20초), gemini-3.5-flash-lite 응답 1.2초 — 글 생성은 3.8-flash, 빠른 보조 작업은 lite 후보
- 남은 일: templates/prompt_template.py, src/ai_generator.py

## 2026-11-02 자료 기반 글·쇼츠 대본 생성기 (지시: 사용자 / 작업: Claude / 검토: Codex 5회 — REVIEW.md)
- 결정: Gemini 구글 검색은 무료 키에서 막힘(3.x는 429, 2.5는 새 사용자 404 직접 확인) → "자료 찾기"와 "글쓰기" 분리. 자료 찾기는 NAVER API HUB(사용자 가입 진행 중) 또는 Tavily 예정, 시트 참고 주소 입력도 지원 예정
- 추가: `src/source_fetcher.py`(공식 페이지 본문 추출, 내부 주소 차단·IP 고정·시간 제한), `src/content_schema.py`(블로그+쇼츠 JSON 형식), `templates/prompt_template.py`(사실·문체 규칙), `src/ai_generator.py`(재시도·예비 모델 gemini-3.5-flash-lite·품질 검사·숫자 대조 경고), `src/blog_html.py`(안전한 HTML, 출처 목록), `scripts/dry_run_content.py`(발행 없이 결과 파일 생성)
- 실제 검증: 국세청 근로장려금 페이지 3개로 생성 성공 2회(55~72초, 1회차 통과). 본문 숫자·날짜·전화번호·조정률을 원문과 직접 대조해 일치. 자료 없이 물으면 신청기간을 틀리게 답함(5.31 vs 공식 6.1) → 자료 기반 필요성 확인
- 결과 파일: `data/dryrun/20261102-011815/`(Git 제외)
- 테스트: 86개 통과
- 남은 일: 검색 서비스 연결(NAVER API HUB 키 대기), 표에서 일부 행만 뽑을 때 원래 기호(가·나·라…) 빼도록 지시문 보완 검토, 다음 구현 tts_engine.py(.ass 자막) → stock_media.py → video_renderer.py → wp_client.py(작업 번호 표시로 중복 초안 방지 필수)

## 2026-11-02 네이버 검색 연결(공식 자료 자동 찾기) (지시: 사용자 / 작업: Claude / 검토: 없음 — 외부 검색 호출 추가, 보안 경계(source_fetcher)는 변경 없음)
- 사용자가 NAVER API HUB(console.ncloud.com)에서 웹문서·뉴스·검색어트렌드 신청, 키를 `.env`의 NAVER_CLIENT_ID/SECRET에 저장(config SECRET_FIELDS에 secret 추가)
- 공식 문서 확인: 호스트 `https://naverapihub.apigw.ntruss.com`, `/search/v1/webkr`, `/search/v1/news`(sort=date), `POST /search-trend/v1/search`, 헤더 `X-NCP-APIGW-API-KEY-ID`/`X-NCP-APIGW-API-KEY`. 한시 무료(유료 전환 시 사전 공지 예정), 한도 문서마다 일 25,000회/월 775,000회로 다름
- 추가: `src/source_finder.py`(웹문서 결과 중 .go.kr·gov.kr·korea.kr·주요 공공기관만 근거 후보, websquare 등 JS 페이지 제외, 뉴스·트렌드 조회), `fetch_sources`에 본문 중복 제거·max_sources
- 생성기 개선: 장면 수×장면당 글자 수 안내, 쇼츠 길이만 틀리면 대본만 고쳐 쓰기(블로그 재작성 없음), 최근 뉴스 제목을 "변경 신호"로 전달(숫자 근거 금지, 자료와 다르면 도입부에 확인 필요 명시)
- 실제 검증: 주제만으로 "청년도약계좌 가입 조건과 혜택" 생성 성공. 처음엔 2024~25년 자료로 현재 가입 가능한 것처럼 씀 → 뉴스 신호 반영 후 도입부에 2025년 12월 가입 종료 언급·최신 확인 안내, 기존 가입자 중심으로 바뀜. 테스트 105개 통과
- 알려진 한계: 서버가 일본(오라클)이라 서민금융진흥원(kinfa.or.kr) 등 일부 공공기관이 해외 접속 차단(연결 시간 초과). 청년센터 등 JS 페이지는 본문 수집 불가
- 남은 일: 주제 선정 시 종료·개편된 제도 걸러내기(검색어트렌드·뉴스 활용), tts_engine.py부터 영상 파이프라인

## 2026-11-02 AI 성우·자막 엔진 (지시: 사용자 / 작업: Claude / 검토: 없음 — 외부 입력 경계 없음, 로컬 파일 생성만)
- 추가: `src/tts_engine.py` — 장면별 edge-tts 합성(단어별 시간), wav로 변환해 0.15초 간격으로 이어 붙임, 장면별 시작·끝 시간, 말하는 타이밍 자막(.ass, 문장부호·쉼·글자 수 기준, 숫자+단위·연속 숫자 안 끊음), 목표보다 12% 넘게 길면 말 속도 올려 재합성(최대 +25%), 네트워크 오류 재시도. 결과 narration.wav / captions.ass / timeline.json
- 보정: 실측 대본 200자 = 31.5초 → CHARS_PER_SECOND 5.0 → 6.2 (45초 쇼츠 대본 목표가 약 223~321자로 늘어남)
- 실제 검증: 근로장려금 대본 5장면 합성 6초, 단색 배경+자막+음성 1080x1920 시험 영상 렌더 17초(nice), 캡처에서 한글 굵은 글꼴·테두리·위치 정상. 테스트 124개 통과
- 결과 파일: `data/dryrun/20261102-011815/tts/`(Git 제외)
- 진행 중: 해외 접속 차단(kinfa 등) 해결책 조사를 포크 세션이 조사만 진행(코드 변경 없음)
- 남은 일: 자막 크기 미세 조정(필요 시), Pexels 키 받으면 stock_media.py → video_renderer.py

## 2026-11-02 해외 접속 차단 공공기관 자료 대신 읽기 (지시: 사용자 / 작업: Claude / 검토: Codex 2회, 최종 PASS — REVIEW.md)
- 문제: 서버가 일본이라 kinfa.or.kr(서민금융진흥원) 등 해외 차단 사이트 연결 시간 초과
- 해결: 직접 읽기 실패 시 Gemini url_context(구글 서버가 가져옴, 무료 lite 모델)로 본문을 옮겨 적게 함. `src/remote_reader.py`. 결과 Source.via="gemini", 생성 경고 "AI가 옮겨 적은 자료(원문 대조 권장)"
- 보안: 정책 거절(내부 주소·포트·형식·계정 URL)은 BlockedUrlError로 대신 읽기 안 함, 대신 읽기 전에도 공인 IP 확인, 대신 읽기 최대 3회·한도/인증 오류 시 중단
- dry run 결과 폴더에 `sources.json`(읽어 온 자료 원문) 저장 → 사람이 대조 가능
- 실제 검증: "청년도약계좌 가입 조건과 혜택" 재생성 → kinfa 2페이지 대신 읽기 성공, 글 도입부에 "신규 가입 2025.12.31 종료" 명시. 원문 "※ 운영 종료 상품 / 신규 가입 '25.12.31까지 운영"과 일치. 테스트 130개 통과
- 남은 한계: 청년센터(youthcenter.go.kr)처럼 Gemini도 본문을 못 찾는 페이지 있음
- 남은 일: tts_engine.py부터 영상 파이프라인(Pexels 키 필요: stock_media)

## 2026-11-02 쇼츠 영상 합성기 (지시: 사용자 / 작업: Claude / 검토: 없음 — 로컬 파일만 다룸, 외부 입력은 stock_media 단계에서 검토 예정)
- 이어받기: 포크 세션의 459e4e6(해외 차단 사이트 대신 읽기, Codex PASS)을 pull해 확인, 테스트 130개 통과 확인. 내 3e306c7에 포크의 중간 파일이 `git add -A`로 섞여 들어갔던 문제 → 이후 커밋은 내가 바꾼 파일만 지정해서 올림
- Pexels: 신규 API 키 발급 일시 중단 → Pixabay로 대체 예정(사용자 키 대기)
- 추가: `src/video_renderer.py` — 장면별 배경(영상: 화면 채우기·반복, 사진: 천천히 이동, 없음: 어두운 움직이는 그라데이션) + 성우 + 자막 + 위쪽 제목(노란 글씨 두 줄, `tts_engine.write_ass(title=...)`), 1080x1920 30fps H.264/AAC, nice 15, 서버 전체 한 번에 하나(파일 잠금), 임시 파일로 렌더 후 ffprobe로 크기·소리·길이 확인하고 교체
- 실제 검증: 근로장려금 쇼츠(그라데이션·시험 영상·시험 사진 배경) 32초 영상 렌더 67초, 캡처에서 제목·자막·배경 정상. 테스트 142개 통과(실제 소형 렌더 포함)
- 남은 일: Pixabay 키 받으면 `src/stock_media.py`(장면별 영상 검색·다운로드, 외부 다운로드라 Codex 검토), 그다음 wp_client.py

## 2026-11-02 Pixabay 장면 배경 수집 (지시: 사용자 / 작업: Claude / 검토: Codex 3회, 최종 PASS — REVIEW.md)
- Pixabay 키 `.env` 저장(PIXABAY_API_KEY). Pexels는 신규 키 발급 중단이라 미사용
- 추가: `src/stock_media.py` — 장면별 세로 영상 우선 → 큰 가로 영상 → 세로 사진 → 없으면 그라데이션, 한 쇼츠 안 중복 금지, 검색 결과 24시간 저장, 다운로드는 Pixabay https만·크기/시간 제한·고유 임시 파일, ffprobe로 깨진 파일 거름, cropdetect로 검은 띠 영상 거름, 보관함 3GB 상한·디스크 여유 3GB 검사. 블로그용 `pick_blog_images()`
- `scripts/dry_run_short.py`: dry run 폴더 → 성우·자막 → 배경 → 렌더(발행 안 함)
- 검색어 규칙 보완(content_schema): 특정 나라 지폐·달력·국기·글자 보이는 장면 피하기(달러·March 달력이 나온 문제)
- 렌더 공용 잠금 `data/.render.lock`(Git 제외 폴더)
- 실제 검증: 근로장려금 쇼츠 실제 Pixabay 영상 5개로 32초 렌더(합성 87초, 배경 찾기 3~9초), 결과 7.8MB. 테스트 162개 통과
- 남은 일: 새 규칙으로 글을 새로 만들어 쇼츠 확인, wp_client.py(워드프레스 응용 프로그램 비밀번호 필요, 작업 번호 표시로 중복 초안 방지), telegram_notifier.py, orchestrator.py

## 2026-11-02 워드프레스 임시 글 저장·발행 클라이언트 (지시: 사용자 / 작업: Claude / 검토: Codex 3회, 최종 PASS — REVIEW.md)
- 사용자가 관리자 계정(bigapple117)의 애플리케이션 비밀번호 `auto-publisher` 발급 → `.env`의 WP_USERNAME/WP_APP_PASSWORD 저장(화면 출력 없음). 인증 확인: administrator, 발행 권한 있음
- 확인: REST API·애플리케이션 비밀번호 인증 켜짐, 업로드 한도 64MB(PHP·nginx). 사이트 이름 "테스트", 시간대 UTC → 사이트 정리 때 바꿀 것
- 추가: `src/wp_client.py`(https 강제, 리다이렉트 거부, 작업 표시 주석으로 중복 방지, 전 페이지 검색, 작업별 잠금, 발행 글 수정 금지, 발행 두 번 눌러도 안전, 미디어 jcjob-<id>-<n> 이름으로 재업로드 방지, 오류에 비밀번호 미노출), `scripts/dry_run_publish.py`(임시 글로만 올림, 사진·출처 기록 재사용)
- 실제 검증: 근로장려금 글을 임시 글 13번으로 저장(사진 6장·표 3개·참고 자료·대표 사진·요약·영문 주소). 4회 재실행해도 글 1개·사진 6장. 발행하지 않음. 테스트 182개 통과
- 남은 일: 사용자에게 임시 글 13번 미리보기 확인 요청, telegram_notifier.py(미리보기·승인), orchestrator.py(장부 db.py와 전체 연결, 시작 시 recover_interrupted), 사이트 기본 정리(이름·시간대·개인정보 처리방침·예제 글)

## 2026-11-02 블로그 사진 크기 문제 수정 (지시: 사용자 / 작업: Claude / 검토: 없음 — 화면 표시 개선, 보안 경계 변경 없음)
- 사용자 캡처: 사진 비율이 제각각이고 글보다 지나치게 커 보임, 대표 사진이 본문 첫 사진으로 한 번 더 나옴
- 수정: `normalize_blog_image()`로 본문 사진을 가운데 기준 16:9, 1200x675 JPEG(품질 82)로 통일(용량 68~140KB). img에 width/height·wp-image-<id> 클래스 → 워드프레스가 srcset(화면별 크기) 자동 적용. 대표 사진은 본문에 다시 넣지 않음
- 실제 검증: 새 시험 임시 글 22번(작업 900002) — 대표 사진 1장 + 본문 5장, srcset 5개. 테스트 184개 통과
- 정리 대기: 예전 시험 임시 글 13번과 그 사진 6장(jcjob-900001-*)은 사용자 확인 후 삭제

## 2026-11-02 사람이 쓴 블로그처럼 글·화면 개편 (지시: 사용자 / 작업: Claude / 자문: Gemini·Codex / 검토: Codex 2회 — REVIEW.md)
- 사용자 피드백: 글과 표 가독성 낮음, AI 블로그 같음 → Gemini(조사)·Codex(설계) 자문 후 종합, 사용자 승인(1번: 사이트 공통 CSS 포함)
- 스키마(`src/content_schema.py`): 소제목 = 블록 목록(paragraph/table/checklist/note, 순서 자유), 문단은 spans(strong 불리언으로만 강조), takeaway(핵심 한 줄, 2~3곳), image_query 선택(글 전체 최대 2곳), intro·closing도 spans
- 지시문: 사전식 도입 금지·독자 문제부터, 표=숫자 비교 / 본문=구분법·예외·다음 행동(표 행 재나열 금지), 문단 1~3문장 250자, 같은 종결 3연속 금지·안내형 섞기, 강조 소제목당 2곳, 법령 용어 풀어쓰기, 소제목 질문·행동·명사형 혼합, 마무리=다음 행동, 거짓 1인칭 금지
- 자동 검사(`check_style`): 표 반복(같은 문장 안 행 이름+숫자 토큰, 60%), 긴 문단(300자/4문장 초과 불합격, 250자 경고), 강조 과다, 사진 과다, 사전식 도입, 블록 불완전, 같은 어미 3연속·같은 구성 3연속(경고)
- 렌더러: `<div class="jc-article">` 래퍼, 표 caption 위·th scope·가로 스크롤 래퍼·4열 wide·짧은 첫 열 key, 주의/확인 요령 상자, 핵심 요약 상자, 마무리 구분선
- 사이트 CSS: 원본 `deploy/wp/jc-article.css`, `scripts/apply_site_css.py`로 워드프레스 전역 스타일(테마 twentytwentyfive, id 6) 추가 CSS에 `/* jalcheck:start~end */` 구간만 넣음. 적용 전 백업 `~/backups/2026-11-02-site-css/`. 되돌리기: 백업 JSON의 styles를 다시 넣거나 사이트 편집 → 스타일 → 추가 CSS에서 해당 구간 삭제
- 실제 검증: 근로장려금 글 재생성(도입부 독자 상황 중심, 표·본문 중복 없음, 주의·확인 요령 상자, 사진 2곳) → 임시 글 26번(작업 900003). 테스트 195개 통과
- 문제: Gemini 3.8 Flash 503/504가 잦아 이번 생성에 24분(504 한 번에 약 5분 대기). 요청 시간 제한·예비 모델 전환 시점 조정 필요
- 화면 캡처 도구: playwright(.venv, requirements 미포함)와 headless Chromium(`tools/pw-browsers`, 280MB, Git 제외) 받았으나 시스템 라이브러리(libatk 등) 없어 실행 불가 — 설치는 sudo 필요해 보류(사용자 승인 대기)
- 정리 대기: 시험 임시 글 13·22번과 그 사진(jcjob-900001-*, 900002-*)은 사용자 확인 후 삭제

## 2026-11-02 PC 본문 폭·모바일 표 수정, 화면 캡처 도구, 시험 글 정리 (지시: 사용자 / 작업: Claude / 검토: 없음 — 표시 CSS·도구·시험 데이터 정리)
- 사용자 피드백: 26번 글 내용은 괜찮으나 PC에서 좌우를 꽉 채워 읽기 힘듦
- 원인: jc-article.css의 `max-width: 100%`가 테마(Twenty Twenty-Five) 본문 폭 제한을 덮어씀(실측 본문 폭 1340px) → `min(100%, --wp--style--global--content-size)` + 가운데 정렬로 수정(실측 645px). 2~3열 표 min-width 0(휴대폰 화면에 맞춤), 4열 표는 가로 스크롤 유지. `scripts/apply_site_css.py`로 재적용(백업 자동)
- 화면 캡처: 사용자 승인으로 Chromium 필수 시스템 라이브러리 8개만 apt 설치(libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libasound2t64 libatk1.0-0t64 libatspi2.0-0t64 libgbm1). 한글 글꼴은 프로젝트 글꼴을 `~/.local/share/fonts`에 링크(계정 한정). `scripts/screenshot_post.py <글번호>`: 공개 글 페이지에 임시 글 본문을 끼워 실제 테마 그대로 PC(1440)·모바일(390) 캡처, 본문 폭·가로 넘침 출력 → `data/screenshots/`. playwright는 .venv에 설치(requirements-dev 성격, 브라우저는 tools/pw-browsers)
- 사용자 승인으로 시험 임시 글 13·22번과 사진 12장(jcjob-900001-*, 900002-*) 영구 삭제(삭제 전 상태·작업 표시 확인). 남은 임시 글은 26번(작업 900003)
- Gemini 대기 개선: 같은 모델에서 시간 초과(504) 2번이면 바로 예비 모델로(MAX_TIMEOUTS_PER_MODEL). 테스트 196개 통과
- 남은 일: 텔레그램 승인 알림, 오케스트레이터(전체 연결), 사이트 기본 정리(이름 "테스트"·시간대 UTC·개인정보 처리방침·예제 글)

## 2026-11-03 텔레그램 승인 알림 (지시: 사용자 / 작업: Claude / 검토: Codex 2회 — REVIEW.md)
- 사용자가 봇 `@jalcheck_publish_bot`(잘체크 발행 알림) 생성, 토큰·개인 대화방 번호를 `.env`(TELEGRAM_BOT_TOKEN/CHAT_ID)에 저장
- `src/telegram_notifier.py`: 승인 요청(글 화면 캡처 파일 → 쇼츠 영상 → 제목·요약·확인할 것·워드프레스 편집 링크 + [발행][보류][취소]), getUpdates long polling(포트 안 엶), 버튼 = 행동:작업:요청nonce:HMAC, 개인 대화방·본인만 수락, 토큰 미노출
- 임시 글 미리보기 링크가 모바일 비로그인에서 404 → 글 화면 캡처(scripts/screenshot_post.py)를 파일로 함께 보내고 링크는 편집 화면(로그인 유도)으로 변경
- 실제 검증: 승인 요청 2회 전송, 사용자가 보류·취소·발행 버튼 눌러 모두 수신(첫 시험은 수신기가 꺼져 있어 버튼이 '로딩'으로 보였음 → 운영 시 상시 수신 필요). 시험이라 발행 안 함
- 남은 일: orchestrator.py — 장부(db)와 전체 연결: 작업 등록 → 자료 → 글 → 쇼츠 → 임시 글 → 승인 요청(nonce 저장) → 버튼 수신(현재 nonce·상태 확인, begin_publishing으로 원자 선점) → 발행/보류/취소, 시작 시 recover_interrupted, 상시 실행(systemd 등은 사용자 승인 필요)

## 2026-11-03 쇼츠 숫자 표기·신청/조회 바로가기 링크·사진 관련성 (지시: 사용자 / 작업: Claude / 검토: Codex 3회 — REVIEW.md)
- 쇼츠: 사용자가 숫자 그대로 읽는 시험 음성 확인(자연스러움) → 대본에 아라비아 숫자 사용(자막도 '4,400만 원'처럼 표시). edge-tts 단어 시간 정보도 숫자 그대로 나옴
- 바로가기 링크: 스키마 `action_links`(label·url·purpose apply/check/info·note), 지시문 "자료 주소나 자료 본문에 적힌 주소만". `src/link_check.py`가 공식 사이트 + 출처 확인(자료 주소 또는 본문에 글자 그대로, 도메인만 적힌 경우 첫 화면만) 통과분만 렌더(최대 4개), 뺀 것은 경고. 도입부 아래 '신청·조회 바로가기' 상자(신청=초록, 안내·조회=파랑), CSS 반영
- 사진 관련성: Pixabay 인기순 결과로 엉뚱한 사진(노트북→팟캐스트 마이크) → 태그 관련도 순(앞 3개 태그 3점, 나머지 1점, 하나도 안 맞으면 제외)
- 실제 검증: 근로장려금 재생성(3분, 1회 통과) → 링크 [신청] 홈택스 https://www.hometax.go.kr/, [안내] 국세청 페이지, 쇼츠 숫자 표기. 임시 글 31번(작업 900004)·쇼츠 39초 렌더(81초)·텔레그램 승인 요청(시험) 전송. 테스트 219개 통과
- 정리 대기: 이전 시험 임시 글 26번(작업 900003)과 사진은 남아 있음

## 2026-11-04 전체 자동 실행기 (지시: 사용자 / 작업: Claude / 검토: Codex 3회, 최종 PASS — REVIEW.md)
- 사용자 승인으로 시험 임시 글 26번·사진 2장 삭제
- 추가: `src/pipeline.py`(단계 함수·Services), `src/orchestrator.py`(장부 상태 진행, 체크포인트로 이어 하기, 텔레그램 버튼 처리, 상시 실행: 작업 스레드+버튼 수신 스레드, 단일 실행 잠금, offset 파일 data/telegram_offset.json), `scripts/jobctl.py`(add/list/show/retry/cancel/once/run), db에 begin_publishing(nonce)·cancel_if_current·stale_publishing
- 첫 실제 작업: 1번 "2026년 자녀장려금 신청 자격과 지급액" → 21분(Gemini 3.8 Flash 503으로 예비 모델 전환, 성우 접속 1회 재시도) → 쇼츠 36초, 임시 글 35번, 텔레그램 승인 요청(nonce 6045e5cd). 경고: 본문 짧음(803자), takeaway 4곳
- 실행: 상시 실행기(jobctl run)는 아직 켜지 않음 → 버튼 처리 안 됨. systemd 서비스 등록은 사용자 승인 필요
- 남은 일: 1번 글 발행 여부 결정, 상시 실행 서비스 등록, 예비 모델 글 품질(본문 짧음) 대응, 사이트 기본 정리(이름·시간대·개인정보 처리방침), 구글 시트 연동(sheet_manager)·웹 대시보드(web_ui)

## 2026-11-04 예비 모델 표시·짧은 본문 재작성·다시 쓰기·상시 실행 준비 (지시: 사용자 / 작업: Claude / 검토: 없음 — 기존 검토 범위 내 소규모 변경)
- 예비 모델로 글을 쓰면 텔레그램 즉시 알림 + 승인 요청 '확인할 것' 맨 위에 "예비 모델 작성 — 품질 확인 필요"(사용자 요청: 알려 줄 것)
- 본문(문단·주의·요약) 1,200자 미만이면 다시 쓰게 함(MIN_BODY_CHARS), 끝내 짧으면 작업 실패 → 텔레그램 알림
- `jobctl redo <번호>`: 글부터 다시(체크포인트 content~approval 삭제, 이 작업이 올린 사진만 삭제, 같은 임시 글 번호로 고쳐 씀, 예전 승인 버튼은 nonce 변경으로 무효)
- 1번 작업 redo(사용자 요청) → 대기열
- 상시 실행: `deploy/systemd/jalcheck-worker.service` 작성(Nice 15, CPUWeight 20, MemoryMax 3G, Restart on-failure). 사용자 승인은 받았으나 Claude Code 자동 안전 장치가 '영구 설정(Unauthorized Persistence)'으로 설치 명령을 차단 → 미설치. 임시로 이 대화 세션의 백그라운드에서 `jobctl run` 실행(최대 2시간, 로그 data/worker-session.log)
- 테스트 238개 통과

## 2026-11-04 상시 실행 서비스 등록 (지시: 사용자 / 작업: Claude / 검토: 없음 — 서버 서비스 등록, 사용자 승인)
- 사용자 요청으로 `~/.claude/settings.json`에 이 서비스 관련 명령만 정확히 허용하는 규칙 10개 추가(cp 단위 파일, daemon-reload, enable/disable/start/stop/restart, status/is-active, journalctl). 기존 알림 훅 유지
- 임시 세션 실행기를 SIGTERM으로 정상 종료 후 `/etc/systemd/system/jalcheck-worker.service` 설치, `systemctl enable --now` → active(running), MemoryMax 3G 적용 확인. 로그: `sudo journalctl -u jalcheck-worker -f`
- 1번 작업 다시 쓰기 결과: 최신 모델(gemini-3.8-flash), 본문 1,623자, 바로가기 [신청] 홈택스·[조회] 심사진행상황, 쇼츠 45.6초, 승인 요청(nonce ef6fe799). 경고: 자료에서 못 찾은 숫자 10만·5%(5%는 '95% 지급'에서 유도된 것으로 보임) — 발행 전 확인 필요
- 주의: 이제 텔레그램 [발행]은 실제 공개 게시

## 2026-11-04 사용자 검수 지적 3건 수정 (지시: 사용자 / 작업: Claude / 검토: 없음 — 표시·검사 오탐·캡처 수정)
- ① 4열 표가 휴대폰에서 잘려 보임(실측 표 576px > 화면 330px, 가로 스크롤 표시 없음, 캡처엔 4번째 칸 누락) → 칸마다 data-label, 600px 이하에서 4열 이상 표는 행별 카드형(열 이름+값 세로)으로 표시. 표 내용은 국세청 자료와 일치 확인
- ② "1일당 10만 분의 22"가 '자료에 없는 숫자'로 경고됨 → 원문 "가산세(1일 22/100,000)"와 같은 사실(오탐). 숫자 대조가 분수(22/100,000·10만 분의 22)를 비율로 인식하도록 수정
- ③ 캡처에서 사진이 빈칸 → 실제 사진은 정상(200), 지연 로딩 전에 캡처된 것. 캡처 전 모든 img를 즉시 로딩·완료 대기
- `jobctl redraft <번호>`: 글·쇼츠는 그대로, 경고 재계산 후 임시 글 HTML만 다시 만들고 새 승인 요청
- 실행기 서비스 재시작(새 코드 반영) 후 1번 redraft. 테스트 240개 통과

## 2026-11-04 사이트 이름·시간대·SEO 기본 설정 (지시: 사용자 "사이트 이름 잘체크, 시간대 한국, 나머지 알아서·구글 SEO" / 작업: Claude / 검토: Codex(SEO 코드) PASS)
- 백업: `~/backups/2026-11-04-site-seo/wordpress-full.sql`
- 설정(REST): 사이트 이름 "잘체크", 설명 "지원금·세금·생활 제도, 공식 자료로 확인한 체크리스트", 시간대 Asia/Seoul, 날짜 "Y년 n월 j일", 시간 "H:i"
- 카테고리 1 "미분류" → "지원금·세금"(slug support-tax, 기본 카테고리), 작성자 표시 "잘체크 편집팀"(slug jalcheck-editor, 로그인 아이디 노출 방지)
- 글 주소 `/%year%/%monthnum%/%day%/%postname%/` → `/%postname%/`(DB permalink_structure 변경, rewrite_rules 재생성). ?p= 주소는 301로 새 주소 이동 확인
- SEO mu-plugin 설치(`wp-content/mu-plugins/jalcheck-seo.php`, sudo install, www-data 소유): 공개 글 meta description(요약문)·OG·Twitter·JSON-LD Article, 첫 화면 WebSite, 카테고리 설명. 다른 SEO 플러그인 있으면 꺼짐
- 글 생성: 태그(핵심 검색어+AI 태그 2~4개, ensure_tags), 사진 대체 텍스트는 image_alt(사진 내용 설명), 글 끝 '잘체크 편집 기준' 상자(공식 자료 확인·확인일·최종 확인 안내·소개 링크)
- 페이지: 소개와 편집 원칙 `/about/` 공개(41), 개인정보 처리방침(3) 초안 임시 저장 — 연락처 이메일 필요(사용자 확인 대기), 예제 페이지(2) 휴지통
- 남은 일: 연락처 확정 → 개인정보 처리방침·문의 페이지 공개, 첫 실제 글 발행 후 기본 글 "안녕하세요!"(1) 삭제(현재 화면 캡처 틀로 사용 중), 검색엔진 차단 해제 시점 결정(지금은 noindex라 사이트 지도 꺼짐) → 해제 후 구글 서치 콘솔 등록·사이트 지도 제출(사용자 구글 계정 필요)

## 2026-11-04 연락처·문의 페이지·머리말/꼬리말·사이트 아이콘
- 지시자: 사용자("jalcheck.help@gmail.com 이메일 만들었어. 또 남은거 있는지 확인해줘") / 작업자: Claude / 검토자: 없음(콘텐츠·화면 변경, 로그인·DB 구조 무관)
- 개인정보 처리방침(3) 연락처 채워 공개, 문의 페이지 `/contact/`(47) 새로 공개, 소개 페이지 끝에 문의 링크 추가
- 꼬리말: 테마 기본 가짜 링크 8개(`#`, 상점·이벤트 등)와 테마 표기를 지우고 소개·문의·개인정보 처리방침 + 안내 문구로 교체. 머리말 메뉴: 지원금·세금 / 소개 / 문의(전엔 모든 페이지 자동 나열)
  - 원본 `deploy/wp/parts/*.html`, 적용 `scripts/apply_site_pages.py`(바꾸기 전 내용은 data/backups/site-pages-*/에 저장). 테마 파일은 그대로, 사이트 쪽 수정본으로 저장됨
- 사이트 아이콘: `assets/brand/site-icon.png`(초록 바탕 "잘" + 체크) 미디어 51, site_icon 설정 → 파비콘·구조화 데이터 logo에 쓰임
- 검증: /, /about/, /contact/, /privacy-policy/, /category/support-tax/ 모두 200, 화면 캡처로 메뉴 확인. 이메일은 Cloudflare 이메일 보호로 페이지 원문에서 가려지고 브라우저에서만 보임(스팸 수집 방지, 정상)
- 남은 작업: 35번 글 발행 결정 → 기본 글 "안녕하세요!" 삭제, 글 5~10개 쌓은 뒤 검색엔진 차단 해제 + 서치 콘솔 등록

## 2026-11-04 모델 바쁨 → 30분 뒤 자동 재시도(예비 모델로 블로그 안 씀)
- 지시자: 사용자(1번 승인) / 작업자: Claude / 검토자: Codex(2회, 지적 반영: 되돌리기 한 트랜잭션·DB 실패 시 다음 차례 재시도·generating 단계만)
- 원인: 주제 10개 첫 작업(2번) 중 gemini-3.8-flash가 503 연속 → 예비 모델(flash-lite)이 954~1,056자로 짧게 써 3회 불합격 → 실패
- 변경: ai_generator `ModelBusyError`, 블로그 본문은 기본 모델로만(blog_fallback=False, 쇼츠 고쳐 쓰기는 예비 모델 허용). orchestrator가 ModelBusyError면 `db.requeue_busy`(generating→queued 한 번에)로 되돌리고 30분(BUSY_WAIT_SECONDS) 새 작업 쉼, 텔레그램 알림은 바쁨 구간당 1회
- 검증: 시험 248개 통과(바쁨→대기→재시도, DB 실패 복구, generating 외 거절). 실행기 재시작, 2번 retry
- 대기열: 작업 2~11(주제 10개, 2026-11-04 등록). 발행은 하루 1~2개 권장(사용자 합의)

## 2026-11-04 모델 바쁨 무한 대기 방지 (지시: 사용자 1번 승인 / 작업: Claude / 검토: Codex)
- 원인: 글 쓰는 모델(gemini-3.8-flash)이 503 혼잡이면 30분마다 횟수 제한 없이 재시도 → 계속 붐비면 대기열 전체가 무한 정지, 알림은 첫 1회뿐
- 변경(src/orchestrator.py): 쉬는 간격 15분 고정(처음엔 30분→1시간→2시간으로 늘렸으나, 우리 요청량은 구글 혼잡에 영향이 없어 늘리면 풀린 시점을 늦게 잡을 뿐이라 사용자와 상의 후 고정으로 변경). 한 작업이 처음 바빠진 뒤 12시간 넘게 계속 바쁘면 "12시간 넘게 Gemini가 계속 붐벼 중단" 실패 기록 + 텔레그램 알림(`jobctl retry`로 재시도) 후 다음 작업으로. 실패 기록 시 바쁨 기록(_busy_streak/_busy_notified) 정리
- 예비 모델(flash-lite)로 블로그 본문 쓰지 않는 원칙은 유지
- Codex 지적 3건 중 2건 반영(실패 시 상태 정리). 1건 보류: 바쁨 시작 시각이 메모리에만 있어 실행기 재시작 시 12시간 계산이 초기화됨(재시작은 배포 때뿐이라 수용, 필요하면 DB 저장으로 보강)
- 검증: 시험 249개 통과(15분 간격·12시간 후 실패·다음 작업 진행·안내 재발송)
- 커밋: f8bf4c2, 이후 15분 고정 커밋
- 실행기 재시작: 사용자 승인("재시작해") 후 진행
- 남은 일: GitHub push 실패(Deploy key 거절, `ssh -T github-awp` → Permission denied). 사용자에게 Deploy keys 확인 요청
- 추후 선택지: 한 번 모은 자료를 저장해 재시도 때 글쓰기만 하기(간격 더 줄일 수 있음)
- 추후 선택지: 예비 모델을 lite보다 나은 모델로 바꿔 시험 글 2~3편으로 품질 확인(사용자 보류)

## 2026-11-04 3.6 Flash와 3.8 Flash 품질 비교(시험 글, 발행 안 함)
- 지시자: 사용자(무료 3.6으로 시작할지 판단하려고 비교 요청, 1번 승인) / 작업자: Claude / 검토자: 없음(코드 변경 없음)
- 방법: 3.8 시험 글(data/dryrun/20261103-223041)과 같은 국세청 자료 3개, 같은 주제로 `GEMINI_MODEL=gemini-3.6-flash` 시험 실행 → data/dryrun/20261104-110453
- 결과: 3.6도 한 번에 검사 통과(27초, 3.8은 197초). 숫자·기간·제외 대상이 자료와 일치하고 지어낸 내용은 보이지 않음. 본문은 약 15% 짧고 '본문이 짧습니다' 경고가 붙음. 사업소득 조정률 예시·간주전세금·단독가구 정의·접수 방법 표가 빠짐. 쇼츠 말투가 조금 더 광고 같음("걱정 마세요!")
- 3.6도 처음에는 503 혼잡이 4번 이어짐(약 2분 뒤 풀림). 비교한 시험 글은 1편뿐
- 남은 일: 사용자가 모델을 정하면 .env의 GEMINI_MODEL 반영(실행기 재시작 필요)

## 2026-11-04 블로그 모델 순서: 3.8 Flash 1순위 → 3.6 Flash 차선
- 지시자: 사용자("1순위는 3.8이고 안되면 차선책으로 3.6") / 작업자: Claude / 검토자: Codex(stdin으로 diff 전달, 샌드박스가 파일을 못 읽어서). 지적 1건(blog_fallback=True면 lite도 블로그 작성)은 기존 수동 옵션(기본 꺼짐, 운영 미사용)이라 그대로 둠
- 변경: ContentGenerator에 second_model 추가, 블로그 = [GEMINI_MODEL, GEMINI_SECOND_MODEL(기본 gemini-3.6-flash)]이고 lite는 블로그에 안 씀. 매 작업 1순위부터 시도하므로 3.8 한도가 다시 채워지면 자동 복귀. 429(한도 초과)는 재시도 대기 없이 바로 다음 모델. 둘 다 실패 → 기존처럼 ModelBusyError → 15분 뒤 재시도. 3.6으로 쓰면 '예비 모델(...)' 경고와 텔레그램 알림(문구 앞부분은 jobctl redraft 호환 위해 유지)
- .env는 바꾸지 않음(GEMINI_MODEL=gemini-3.8-flash 유지, 차선은 코드 기본값)
- 검증: 시험 253개 통과(429 → 차선 즉시, lite 미사용, 다음 작업 1순위 복귀). 실제 시험 실행: 3.8 429 → 바로 3.6 → 성공(검사 3회차 통과, '같은 종결 표현 연속' 경고)
- 남은 일: 실행기 재시작(sudo, 자동 권한 검사에 막혀 사용자 허용 대기) 전까지는 실행기가 옛 코드로 3.8만 15분마다 재시도

## 2026-11-04 검사 불합격 시 새로 쓰기 → 앞 결과 고치기
- 지시자: 사용자("불합격이면 새로 쓰기보다 수정이 낫지 않나", 1번 승인) / 작업자: Claude / 검토자: Codex(diff stdin 전달)
- 원인: 2회차부터 지적 사항만 주고 앞 결과는 안 줘서 사실상 새로 씀 → 잘된 부분도 버리고 다른 문제가 새로 생길 수 있음
- 변경: `build_prompt(previous=...)`로 앞 결과 JSON을 함께 주고 "지적된 곳만 고치고 나머지 유지, 단 사실 규칙 우선" 지시. 형식(JSON)이 깨지면 마지막 정상 결과를 같은 지적으로 다시 고치게 함. 처음부터 형식이 깨진 경우만 새로 씀. 최대 3회·실패 처리는 그대로
- Codex 지적 2건: (1) "그대로 두라"가 숨은 오류까지 보존할 수 있음 → 사실 규칙 우선 문장 추가(반영) (2) 지시문 길이 → 앞 결과 1개만 붙고 누적 안 됨, 모델 입력 한도 대비 작아 수용
- 검증: 시험 255개 통과(고치기 지시문 포함, JSON 깨짐 시 앞 결과 재사용). 실제 Gemini로 불합격→고치기 경로는 아직 안 봄(다음 실제 불합격 때 로그 확인)
- 실행기 재시작: 사용자 승인("ㅇ") 후 18:50 UTC 완료
- 재시작(사용자 "재시작 허용") 후 확인: 3.8 429 → 즉시 3.6으로 넘어감. 3.6은 503 뒤 하루 무료 한도 초과(quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier, 비교 시험 글로 소진). 4번은 15분마다 재시도 중
- 18:50 UTC에 실행기 재시작이 한 번 더 있었는데 Claude가 한 게 아님(사용자 확인 요청)
- 사용자 선택 1: 코드 수정 없이 한도가 채워진 뒤(2026-11-05 17:10 KST 예약 알림) 4번이 12시간 규칙으로 failed면 `jobctl retry 4`. 하루 한도 초과는 12시간 실패 규칙에서 빼는 개선안은 보류

## 2026-11-04 검사 불합격(본문 짧음) 줄이기 — 글 1편당 Gemini 호출 줄이기
- 지시자: 사용자(1번 승인: 세 가지 순서대로) / 작업자: Claude / 검토자: Codex(diff stdin, 샌드박스 오류로 BLOCKED, 지적 없음, 미검증 항목은 Claude가 시험으로 확인 — REVIEW.md)
- 조사: 3.8 Flash 호출은 결과물 1개당 기본 1번, 불합격 시 최대 3번(쇼츠 길이 고쳐 쓰기 포함 이론상 9번). 무료 한도는 모델별 하루 20번(quotaValue 20). 통과한 글 7편도 검사 기준 본문이 1,300~1,750자로, 지시문의 '전체 2,000~3,500자'에 못 미침
- 원인: 지시문은 '전체' 글자 수, 검사는 표·FAQ·체크리스트·마무리를 뺀 문단·주의·요약만 셈 → 모델은 충분하다고 여기고 1,200자 미만으로 불합격. 또 slug·금지 표현을 고치는 회차에 본문이 다시 줄어 불합격(작업 1: 1056→통과 길이→954)
- 변경: (1) 지시문을 검사 범위와 같게 '문단·주의·요약만 1,500~2,500자, 소제목마다 300~450자'로 바꾸고 상수 BODY_TARGET_CHARS 공유(f928902) (2) 분량 외 지적으로 고칠 때 '[분량 유지] 지금 N자, 줄이지 말 것' 안내 추가, 최종 불합격 이유에는 미포함(5dba78b, b411a0a) (3) slug 설명에 영어 단어·예시 추가(1896162). slug를 코드로 자동 고치는 안은 실제 사례가 한글 slug 1건뿐이고 한글은 기계적으로 고칠 수 없어 하지 않음
- 검증: pytest 259개 통과. 실제 Gemini 시험 글은 API 키 만료로 못 함
- 남은 일: 새 API 키를 .env에 넣은 뒤 시험 글 1~2편으로 1회차 통과율·본문 글자 수 확인, 실행기 재시작(사용자 승인 필요) 전까지 실행기는 옛 지시문 사용. 키 만료 동안 작업 4번은 15분마다 실패·재시도 중이며 12시간 규칙으로 failed가 될 수 있음(`jobctl retry 4`로 되살림)

## 2026-11-04 Gemini 유료(선불) 전환 확인, 실패 작업 4번 재개
- 지시자: 사용자(AI Pro 월 10달러 크레딧 연결 후 "1" → 이어서 "2"로 변경) / 작업자: Claude / 검토자: 없음(코드·설정 변경 없음, 작업 상태만 변경)
- 확인: .env의 GEMINI_API_KEY는 이미 선불 결제 계정(Cloud Prepay 01BD6B-…) 프로젝트 키였음. 앞 기록의 "키 만료"는 실제로는 선불 잔액 0. 2026-11-04 19:21~19:22 UTC에 작업 4~11번이 402 "prepayment credits are depleted"로 모두 실패
- 사용자가 8,000원 선불 충전(자동 충전 끔 안내) + Developer Program 월 10달러 크레딧(₩13,433, 2027-11-05까지)을 같은 결제 계정에 적용. 무료 체험 잔여 크레딧 ₩414,984(2026-12-20까지)도 같은 계정에 보임(Gemini API 적용 여부는 미확인)
- 검증: 3.8 Flash·3.6 Flash에 짧은 요청 → 둘 다 200 정상
- 작업 상태: 4번만 retry(진행), 5~11번은 retry 직후 사용자 요청으로 cancel 보류. 4번 결과(비용·품질) 확인 후 `jobctl retry 번호`로 재개
- .env, 코드, 실행기는 바꾸지 않음

## 2026-11-05 실행기 재시작 + 새 분량 지시 시험 글 1편
- 지시자: 사용자(1번: 재시작 후 2번 작업 주제를 같은 자료로 다시 쓰기) / 작업자: Claude / 검토자: 없음(코드 변경 없음)
- 바로잡음: 앞 기록의 '키 만료'는 실제로 선불 잔액 0이었고, 954·1,056자 불합격은 2번 작업을 예비 모델(flash-lite)이 썼던 때의 일
- 실행기 재시작: 2026-11-05 00:03 UTC(진행 중 작업 없음 확인 후). 이제 f928902~b411a0a 지시문 적용
- 시험: 2번 작업 주제·자료 4개 그대로 → data/dryrun/20261105-000405 (발행 안 함)
- 결과: 3.8 Flash 1회차 통과(35초, 호출 1번). 본문 1,741자(예전 3.8) → 2,646자, 소제목별 338~564자, slug 영문 정상. 본문 숫자 29개 모두 자료에서 확인(7,000만 원 = 자료의 7천만원). 경고는 '자료로 확인 안 된 내용' 목록뿐
- 비용 추정: 입력 17,226토큰, 결과 5,254토큰(count_tokens, 생각 토큰 미포함) × 3.8 Flash 현재 단가($0.75/$3.75 per 1M, 제3자 가격표 기준·2027-01-01 두 배 예정) ≈ $0.03~0.05/편(생각 토큰 포함 추정)
- 남은 일: 시험 1편뿐이라 실제 작업 몇 편에서 1회차 통과율 계속 확인, 5~11번 재개 여부는 사용자 결정

## 2026-11-05 글 소제목 위 간격 넓힘(가독성)
- 지시자: 사용자(휴대폰 캡처로 소제목 앞 간격 요청, 1번 승인) / 작업자: Claude / 검토자: 없음(화면 CSS 한 줄, 로그인·DB 구조 무관)
- 측정(iPhone 13 화면, 1번 발행 글): 소제목 위 48px, 문단 사이 20px → 앞 상자(확인 요령·바로가기)와 다음 소제목이 붙어 보임
- 변경: deploy/wp/jc-article.css `.jc-article h2` margin-top 2.4em → 3.4em(약 68px), `scripts/apply_site_css.py`로 적용. 백업 ~/backups/2026-11-05-site-css/global-styles-6-000927.json
- 검증: 실제 사이트에서 소제목 7곳 모두 68px, 문단 사이 20px 그대로, Cloudflare 캐시 DYNAMIC(바로 반영)
- 되돌리기: 2.4em으로 고쳐 같은 스크립트 재실행, 또는 백업 JSON 복원

## 2026-11-05 유튜브 채널 프로필 사진·배너 제작
- 지시자: 사용자(유튜브 채널 만들기, 계정 jalcheck.help@gmail.com) / 작업자: Claude / 검토자: 없음(이미지 파일만, 운영 영향 없음)
- 파일: `scripts/make_youtube_brand.py` → `assets/brand/youtube/profile.png`(800x800, 원형으로 잘려도 글자·체크가 안에 들어가게), `banner.png`(2560x1440, 글자는 모든 기기에서 보이는 가운데 1546x423 안), `banner-safe-area-check.png`(확인용 빨간 선)
- 블로그 주소는 https://jalcheck.com (jalcheck.help는 Gmail 아이디일 뿐, 도메인 아님)
- 채널 쪽 상태: 새 계정이라 2단계 인증·전화번호 인증이 "본인 인증을 할 수 없습니다"로 막힘 → 처음 만든 기기·집 와이파이로 며칠 뒤 재시도 안내
- 남은 일: 채널 핸들이 정해지면 블로그 소개 페이지에 유튜브 링크 추가

## 2026-11-05 블로그에 유튜브 채널 링크 추가
- 지시자: 사용자(핸들 @jalcheck 전달, 1번 승인) / 작업자: Claude / 검토자: 없음(링크 2곳 추가, 로그인·DB 구조 무관)
- 채널: https://www.youtube.com/@jalcheck (채널 ID UC7GknL7CWLKh0pm08AmPKxg)
- 변경: deploy/wp/parts/footer.html 꼬리말 메뉴에 "유튜브"(새 창), deploy/wp/pages/about.html에 "짧은 영상으로도 정리합니다" 문단. `scripts/apply_site_pages.py`로 적용(문의·개인정보·머리말은 원본과 같아 내용 변화 없음)
- 백업: data/backups/site-pages-20261105-005106
- 검증: /about/·/ 200, 본문·꼬리말 링크 각 1개(target=_blank), 모바일(390) 캡처로 확인

## 2026-11-05 발행 직후 유튜브 쇼츠 업로드 안내(영상·제목·설명) 텔레그램 전송
- 지시자: 사용자("원해") / 작업자: Claude / 검토자: Codex(diff stdin, 지적 1건 반영: 장부 기록 실패 시 정상 경로와 reconcile에서 두 번 보낼 수 있음 → 기록 성공 때만 보내고, 실패면 reconcile이 한 번 보냄)
- 변경: `src/youtube_kit.py`(AI 추가 호출 없이 검사 통과한 쇼츠 제목·검색 설명·기준 시점·글 주소·안내 문구·해시태그 최대 5개(#잘체크 항상 포함)), `src/orchestrator.py` `send_upload_kit` — [발행] 성공 또는 reconcile로 발행 확인 시 쇼츠가 있으면 영상 → 제목 → 설명을 메시지 3개로 전송. 실패해도 발행은 그대로, 텔레그램에 실패 안내
- 검증: pytest 265개 통과(전송, 블로그만 작업 미전송, 전송 실패 시 발행 유지, 중복 방지)
- 이미 발행된 1·2번 작업은 수동으로 업로드 안내 전송 완료
- 실행기 재시작: 사용자 승인("1") 후 2026-11-05 01:13 UTC 완료, active 확인

## 2026-11-05 작업 5~11번 재개(유료 전환 뒤)
- 지시자: 사용자(1번: 5~11번 바로 다시 돌리기) / 작업자: Claude / 검토자: 없음(작업 상태만 변경)
- 00:11 UTC에 retry 5~11. 실제 처리는 03:26~03:51 UTC(시작이 늦은 이유는 확인 못 함, 01:13 다른 대화의 실행기 재시작 이후에도 대기)
- 결과: 7편 모두 3.8 Flash로 승인 대기(글 68·70·73·76·78·80·82). 예비 모델·402·429·503 없음. 1회차 불합격은 7번 1건(표 내용 반복)
- 승인 전 확인할 경고: 6번 숫자 5,200원 / 7번 400만·150만 원 / 8번 148만·118만·3,000만·1,200만 원(자료에서 못 찾은 숫자), 10번 본문 1,487자 짧음, 5번 같은 종결 표현 연속, 9번 takeaway 4곳
- 승인 대기 총 10편(2~11번). 발행은 하루 1~2개 권장

## 2026-11-05 글 서식 디자인 개편(시안 → 실제 사이트)
- 지시자: 사용자(시안 확인 후 1번 "글 서식부터 실제 사이트에 적용" 승인) / 작업자: Claude / 검토자: Gemini·Codex(시안 UX·디자인 검토, 읽기 전용. 결과 data/design-review/gemini.txt, codex.txt — 체크 표시 오해, 휴대폰 3열 표, 소제목 선, 표 제목 등 반영)
- 시안: https://claude.ai/artifact/Jxn78NMoF5iXoz2G3WoGg6 (홈 화면·글 서식·휴대폰 화면 탭). 결정: 흰색 기본(다크모드는 나중에 색 변수만 추가), 홈에 큰 검색창 없음, 홈은 한 줄 소개 뒤 바로 카드
- 변경: deploy/wp/jc-article.css 전면 개편(색 변수 --jc-*, 소제목 왼쪽 선, '핵심' 요약 꼬리표, 신청·조회 큰 버튼, 조건 목록 중립 점 표시, Q 표시 FAQ, 마무리 테두리 상자, 휴대폰 2~3열 표 줄바꿈·4열 이상 카드). 소제목 위 3.4em·문단 1.25em 유지. 글꼴은 아직 테마 기본(Manrope+시스템 한글) 그대로
- 적용: scripts/apply_site_css.py. 백업 ~/backups/2026-11-05-site-css/global-styles-6-063731.json (적용 직전), 063717.json(dry-run 시점)
- 검증: 실제 글 2편 390px·1280px 캡처, 가로 넘침 없음(scrollWidth 390/1280), 소제목 위 68px 유지, 사진 정상(330px, 둥근 모서리), 홈 200
- 되돌리기: 백업 JSON의 styles.css를 복원하거나 git에서 이전 jc-article.css로 되돌려 같은 스크립트 재실행
- 남은 일: (1) 글 목차 접기·"함께 보면 좋은 제도"는 CSS로 불가 → mu-plugin 필요(사용자 승인 후) (2) 홈 화면 개편(테마 템플릿) (3) 글꼴 IBM Plex Sans KR 사이트 전체 적용 여부 (4) 바로가기 문구가 홈택스 첫 화면으로 가는 문제는 글 생성 규칙 쪽(Codex 지적) (5) 광고 위치 원칙은 애드센스 신청 때

## 2026-11-05 글 목차 접기·"함께 보면 좋은 제도" 추가(mu-plugin)
- 지시자: 사용자(설명 후 1번 진행, 설치 확인 1번 승인) / 작업자: Claude / 검토자: Codex(3차 PASS, REVIEW.md)
- 파일: deploy/wp/mu-plugins/jalcheck-reading.php → sudo install로 /var/www/html/wp-content/mu-plugins/jalcheck-reading.php(www-data, 644). deploy/wp/jc-article.css에 .jc-toc·.jc-related 규칙 추가(apply_site_css.py, 백업 ~/backups/2026-11-05-site-css/global-styles-6-065911.json)
- 동작: 공개 글 화면의 the_content(20)에서만, .jc-article 안 h2에 id(jc-sec-N, 기존 id와 안 겹치게) 부여 + 신청·조회 바로가기 아래 접힌 목차(소제목 3개 이상, '참고 자료' 제외). 글 끝에 관련 글 최대 3개(같은 태그 → 같은 카테고리 → 최신), ID만 12시간 transient, 글 공개/수정·태그/카테고리 변경 시 버전 갱신. 발췌문·피드·REST·관리자·쿼리 루프 제외. 글 원본 DB 변경 없음
- 검증: php -l, 실제 본문 목차 생성 시험. 설치 후 홈·소개 200이고 목차/관련 글 0개, 글 2편 각각 목차 1개(6항목, 바로가기 다음), 목차 링크 누르면 소제목으로 이동(화면 위 24px), 관련 글 2개(공개 글이 3편뿐), meta description 정상, 390px 가로 넘침 없음, 휴대폰 캡처 확인
- 되돌리기: sudo rm /var/www/html/wp-content/mu-plugins/jalcheck-reading.php (CSS는 남아도 쓰이지 않음)
- 남은 일: 테마 기본 "더 많은 게시물"·이전/다음 글 링크와 겹침 → 홈 화면 개편 때 정리. 글꼴 변경, 홈 화면 개편

## 2026-11-05 홈 화면·글 화면 틀·머리글 개편 + 글꼴·댓글(기능 파일은 설치 대기)
- 지시자: 사용자(1번 홈 개편+글꼴+겹치는 목록 정리, A1 글 맨 위 대표 사진 빼기, B1 댓글 닫기) / 작업자: Claude / 검토자: 없음(화면 틀·CSS, 로그인·DB 구조 무관. 결과는 실제 캡처로 확인)
- 파일: deploy/wp/templates/home.html·single.html(새로), deploy/wp/parts/header.html(햄버거 대신 메뉴 3개 + 돋보기 검색), deploy/wp/jc-site.css(새로: 글꼴·머리글·홈·글 머리), deploy/wp/pages/privacy.html(Google Fonts 문장), deploy/wp/mu-plugins/jalcheck-theme.php(새로: 구글 폰트 IBM Plex Sans KR 불러오기 + preconnect, comments_open/pings_open false, 화면 댓글 목록 비움 — 아직 서버 미설치)
- 스크립트: apply_site_css.py가 jc-site.css + jc-article.css를 합쳐 넣음, apply_site_pages.py가 templates(home, single)도 저장
- 홈: 한 줄 소개(h1) → 최신 글 1개 크게 → 나머지 최신 9개 카드(사진·카테고리·날짜·요약 2줄) → 제도별로 찾기(태그) → 편집 원칙 띠. 시안의 '핵심 숫자' 꼬리표는 데이터가 없어 제외, 카테고리가 1개라 '주제별' 대신 태그
- 글: 카테고리·제목·잘체크 편집팀·날짜 → 본문 → 태그. 대표 사진·이전/다음 글·더 많은 게시물·댓글 제거(대표 사진은 홈 카드·공유 미리보기에 그대로 쓰임)
- 적용 백업: ~/backups/2026-11-05-site-css/global-styles-6-072345.json(첫 적용 직전), data/backups/site-pages-20261105-072345(머리글·꼬리말·템플릿·페이지)
- 검증(390·1280 캡처): 홈·글·소개·카테고리 200, 가로 넘침 없음, 홈 대표 1·카드 2·태그 8, 글 화면 대표사진·댓글·이전다음 0, 목차 1·관련 글 2, 검색(돋보기→입력→결과 6개) 정상. 고친 것: 제목 단어 중간 줄바꿈(코어 break-word 덮어씀), 메뉴 글자 세로 정렬, 휴대폰에서 열린 검색창이 로고와 겹침
- 되돌리기: 관리자 > 디자인 > 템플릿/템플릿 파트에서 '사용자 정의 초기화', CSS는 백업 JSON 복원
- 남은 일: jalcheck-theme.php sudo 설치(사용자 확인 필요) — 설치 전에는 글꼴이 대체 글꼴(애플/맑은 고딕), 댓글은 화면에서만 사라졌고 직접 보내는 접수는 아직 열림. 카테고리·검색 결과 화면은 아직 테마 기본(제목 '[카테고리:]' 표기 등) → 다음에 정리

## 2026-11-05 글꼴·댓글 기능 파일 설치
- 지시자: 사용자(설치 1번 승인) / 작업자: Claude
- sudo install로 /var/www/html/wp-content/mu-plugins/jalcheck-theme.php(www-data, 644)
- 검증: 홈·글·소개 200, /wp-admin/ 302(정상 로그인 이동), 구글 폰트 link·preconnect 출력, 휴대폰 화면에서 IBM Plex Sans KR 글꼴 조각 로드 확인(홈 33·글 43개 조각), 가로 넘침 없음, 댓글 직접 접수(wp-comments-post.php) 403으로 막힘
- 되돌리기: sudo rm /var/www/html/wp-content/mu-plugins/jalcheck-theme.php
- 남은 일: 카테고리·검색 결과 화면(테마 기본) 정리

## 2026-11-05 카테고리·태그·검색 결과 화면 정리
- 지시자: 사용자(1번) / 작업자: Claude / 검토자: 없음(화면 틀·CSS)
- 파일: deploy/wp/templates/archive.html·search.html(새로, 홈과 같은 카드 + 쪽 번호), jc-site.css(목록 머리·검색창·쪽 번호 규칙), apply_site_pages.py TEMPLATES에 archive·search 추가
- 바뀐 점: 제목 '[카테고리:]' 표기 제거(카테고리·태그 이름만), 카테고리 설명 표시, 검색 결과 화면에 검색창, 결과 없을 때 안내 문장 + 홈으로 가기
- 백업: data/backups/site-pages-20261105-212540·212619, ~/backups/2026-11-05-site-css/global-styles-6-212540.json
- 검증(390·1280): 카테고리 카드 3, 태그(자녀장려금) 1, 검색 '연말정산' 3, 결과 없음 안내, 모두 200·가로 넘침 없음

## 2026-11-05 문화누리카드 글(59번) 검토·수정 + 지원 대상·공식 링크 규칙 보강
- 지시자: 사용자(대상 누락·강동구청 링크 지적, 1번 "글과 프로그램 함께 수정" 승인) / 작업자: Claude / 검토자: 없음(글 문구·글 작성 규칙, 로그인·DB 구조 무관)
- 원인: (1) 원고에 "기초생활수급자·차상위계층만 대상"이 빠져 누구나 받는 것처럼 읽힘 (2) 공식 누리집 mnuri.kr이 .go.kr이 아니고 공식 목록에도 없어 자료·링크로 못 써서 강동구청 페이지가 바로가기에 걸림
- 글 수정(DB 직접 UPDATE): 도입부·지원 금액 소제목에 대상 명시, 표 '기본 지원' 칸 수정, 바로가기 → https://www.mnuri.kr/, 오타 '본인 충금', FAQ의 내부 표시(S1/S2) 제거·자동 재충전 조건(2025년 3만 원 이상 사용+자격 유지) 정리, 발급 FAQ에 대상·예산 소진 조기 마감, 참고 자료에 공식 누리집·금천구청 추가, 요약문(excerpt)에 대상 추가
- 근거: 정책브리핑 148959372(2026.02.23), 금천구청 contents.do?key=944. mnuri.kr 본문은 자바스크립트라 직접 못 읽음
- 백업: ~/backups/2026-11-05-post59/(wp_posts-59.sql, post59-content.html). 되돌리기: sudo mysql wordpress < wp_posts-59.sql
- 프로그램: source_finder 공식 목록에 mnuri.kr·arko.or.kr, content_schema에 eligibility(지원 대상) 필드, blog_html이 '기준' 아래 '대상:' 줄 표시, ai_generator가 대상이 비면 경고, 프롬프트에 대상 명시 규칙·전국 공식 누리집 우선 규칙. 테스트 추가
- 검증: pytest 268 통과, 실제 글 200·공식 링크 2곳·S1/S2/오타 0·meta description 갱신, 휴대폰 390px 가로 넘침 없음·캡처 확인, 홈 200
- 남은 일: 다른 공개 글도 대상 누락·지자체 링크 여부 점검. 공식 목록은 새 제도 사이트가 나올 때마다 추가 필요

## 2026-11-06 공개 글 검토(수정 없음) 후 55번 연말정산 글 수정
- 지시자: 사용자(공개 글 검토만 → 1순위 55번 글 수정 승인) / 작업자: Claude / 검토자: 없음(글 문구)
- 검토 결과(미수정): 35번 자녀장려금 글에 기한 후 신청 마감일 없음, 홈택스 첫 화면 링크, 참고 자료 제목에 기관명 없음 / 57번 고향사랑 글에 12월 31일 기한 문장 없음 / 임시 글 68·76 링크 이름과 실제 주소 불일치, 70 에너지바우처에 서울시 페이지, 78 서울시 이택스
- 55번 수정: 주택청약 소득공제 대상(총급여 7천만 원 이하 무주택 세대주, 세대원 불가) 명시, 금융상품 표 열 이름 '연간 납입 한도' → '공제 대상 납입액 한도', 연금 요약 문장, 고향사랑기부금 2026년 기준(10만~20만 원 44%, 20만 원 초과 16.5%·특별재난지역 33%)으로 57번 글과 맞춤, 12월 31일 기부 기한·주소지 제외 문장 추가
- 백업: ~/backups/2026-11-06-post55/(wp_posts-55.sql, post55-content.html). 되돌리기: sudo mysql wordpress < wp_posts-55.sql
- 검증: 글 200, 고친 문구 반영·옛 문구 0, 휴대폰 390px 가로 넘침 없음·표 캡처 확인
- 확인 못 한 것: 44%·특별재난지역 33%는 공식 원문 미확인(여러 2차 자료와 57번 글 기준). 2026년 귀속 기본공제 소득요건 완화(100만→300만 원) 주장이 블로그에 있으나 공식 확인 못 해 FAQ의 100만 원은 그대로 둠
- 남은 일: 35번(기한 후 신청 마감일), 57번(기한 문장), 임시 글 링크 정리

## 2026-11-06 35번 자녀장려금 글: 기한 후 신청 마감일 추가
- 지시자: 사용자("응") / 작업자: Claude / 검토자: 없음(글 문구)
- 근거: 국세청 신청기간 및 방법(mi=40397, cntntsId=238977) — 2026년 정기신청 5.1.~6.1., 기한 후 신청 6.2.~12.1.(블로그 일부는 11.30.로 표기, 국세청 기준을 씀)
- 수정: 도입부·요약·본문·마무리에 "2026년 12월 1일까지 기한 후 신청" 명시, 틀린 문장 "매년 5월 1일~5월 31일" → 2026년 5월 1일~6월 1일, 바로가기 문구를 실제 이동 위치(홈택스 첫 화면)에 맞게 "국세청 홈택스 바로가기", 참고 자료 제목에 기관명 + 국세청 신청기간 페이지 추가
- 백업: ~/backups/2026-11-06-post35/. 되돌리기: sudo mysql wordpress < wp_posts-35.sql
- 검증: 글 200, '12월 1일' 5곳·옛 문장 0, 390px 가로 넘침 없음·캡처 확인
- 남은 일: 57번(12월 31일 기부 기한 문장), 임시 글 링크 정리

## 2026-11-06 57번 고향사랑기부제 글: 12월 31일 기부 기한 추가
- 지시자: 사용자("응") / 작업자: Claude / 검토자: 없음(글 문구)
- 수정: '세액공제 적용 전 체크할 주의점'에 "기부한 해 기준 공제, 2026년분은 12월 31일까지 기부·결제, 1월 기부는 다음 해로" 문단, 마무리 문장에 기한 한 줄
- 백업: ~/backups/2026-11-06-post57/. 되돌리기: sudo mysql wordpress < wp_posts-57.sql
- 검증: 글 200, 추가 문장 2곳 확인
- 남은 일: 임시 글 링크 정리(68·70·76·78)

## 2026-11-06 임시 글 바로가기 링크 정리(68·70·76)
- 지시자: 사용자("응") / 작업자: Claude / 검토자: 없음(글 문구, 모두 draft 상태 유지)
- 68: "홈택스 심사진행상황 조회"(첫 화면으로 감) → "국세청 홈택스 바로가기", 설명에 "로그인 후 장려금 메뉴에서 심사진행상황 조회"
- 70: 서울시 뉴스 페이지 → 에너지바우처 공식 누리집 https://www.energyv.or.kr/ (접속 200 확인). 기준 문구·참고 자료의 서울시 자료는 실제 근거라 그대로 둠
- 76: "국세청 홈택스 소득·세액공제 조회"(실제는 국세청 설명 페이지) → "국세청 연금계좌 세액공제 안내", 꼬리표 조회→안내
- 78: 서울시 이택스는 "서울시 등록 차량 신청"으로 이미 구분돼 있어 그대로 둠
- 백업: ~/backups/2026-11-06-drafts/wp_posts-68-70-76.sql
- 남은 일: energyv.or.kr을 공식 목록(source_finder)에 추가할지, 첫 화면 주소에 '조회·신청하기' 같은 이름이 붙지 않게 하는 규칙(예전 Codex 지적) — 사용자 결정 대기

## 2026-11-06 바로가기 링크 규칙 보강(에너지바우처 공식 인정, 첫 화면 링크 이름 경고)
- 지시자: 사용자(1번) / 작업자: Claude / 검토자: 없음(글 작성 규칙·경고, 로그인·DB 무관)
- 파일: src/source_finder.py(energyv.or.kr 공식 목록), src/link_check.py(home_links_overpromising: 첫 화면 주소인데 이름에 신청·조회·발급·납부·등록·확인이 있고 '바로가기'가 없으면 표시), src/ai_generator.py(승인 요청 경고로 보냄), templates/prompt_template.py(첫 화면 링크는 '○○ 바로가기', 메뉴 안내는 note), 테스트 2건
- 검증: pytest 270 통과
- 참고: 경고만 띄우고 글을 막지는 않음(78번 '위택스에서 자동차세 연납 신청하기' 같은 경우도 경고 대상)

## 2026-11-06 공식 사이트 판단: 정부 자료가 안내한 누리집 자동 인정
- 지시자: 사용자(1번) / 작업자: Claude / 검토자: Codex(3차 PASS, REVIEW.md)
- 이유: 공식 목록(source_finder OFFICIAL_HOSTS)이 하드코딩이라 .go.kr이 아닌 사업 누리집(mnuri.kr 등)이 빠지면 구청 링크가 걸림
- 변경: src/link_check.py — 서로 다른 공식 사이트 자료 2곳 이상(서버가 직접 읽은 자료만)의 본문에 도메인이 적힌 사이트는 첫 화면 주소를 바로가기로 인정. 깊은 주소·단축 주소는 이 방식으로 인정 안 함. 기존 .go.kr 규칙·고정 목록은 기본값으로 유지. 프롬프트에 안내 추가. 테스트 4건(test_link_check 3, test_content 리다이렉트 1)
- 한계: 바로가기 링크에만 적용. 검색으로 자료를 고르는 단계(find_official_urls)는 여전히 고정 목록 기준. 정부 자료 1곳에만 적힌 누리집은 인정 안 됨
- 검증: pytest 274 통과

## 2026-11-06 작업 처리기 재시작 + 승인 대기 글 7편 검토·수정
- 지시자: 사용자(1번) / 작업자: Claude / 검토자: 없음(글 문구, 모두 draft 유지)
- 재시작: jalcheck-worker가 11-05 01:13 UTC부터 떠 있어 오늘 바꾼 규칙(대상 필드, 링크 규칙 등)이 메모리에 반영 안 된 상태였음 → sudo systemctl restart(10:37 UTC), "실행기 시작" 로그 확인
- 수정(백업 ~/backups/2026-11-06-drafts-2/wp_posts-drafts.sql):
  - 82 실업급여: 비자발적 이직 요건(자발적 퇴사·중대 귀책 해고 제외, 정당한 사유 예외) 추가 — 대상 누락. 오타 '산약'→'산입', 마무리 문장
  - 70 에너지바우처: 표 오른쪽 열 의미 정정(연탄쿠폰·연탄전환·긴급복지 연료비 받는 세대는 하절기분만, 동절기 중복 불가), 하·동절기 구분 없이 7.1~5.31 사용 가능(여름 미사용분 겨울 사용) — energyv.or.kr 지원 안내 확인. 기준 문구·참고 자료에서 서울시 → 공식 누리집
  - 78 자동차세: 2027년 1월 연납 공제율·기간 미발표 안내 추가(현행 5% 산식, 2027 미확정)
  - 76 연금계좌: 12월 31일 입금분까지 공제 문장
  - 68 반기 근로장려금: 재산 기준 문장(상반기 2025년, 정산 2026년 요건) 정리
  - 전부: 참고 자료 제목에 기관명(주소 그대로 노출·메뉴 경로 제목 정리)
- 확인 못 함(수정 안 함): 73 카드 납부 한도 1천만 원·수수료율(0.7/0.4%) 최신 여부, 78 연세액 10만 원 미만 차량 1·3월만 연납(자료 엇갈림), 80 청년미래적금 세부(공식 원문 미확인, 현재 가입 신청 시기 안내 없음), 35·68 반기신청자 자녀장려금 지급 시점
- 화면 확인: 임시 글이라 로그인 없이 미리보기 불가 → DB 내용 일치만 확인

## 2026-11-06 승인 대기 글 미확인 항목 조사·수정(73·80)
- 지시자: 사용자("응") / 작업자: Claude / 검토자: 없음(글 문구, draft 유지)
- 73 종합부동산세: 국세청 신용카드납부 안내(mi=2365, cntntsId=7742) 원문 — 수수료 신용카드 0.4~0.8%, 체크카드 0.15~0.5%, 대상 모든 세목, 금액 한도 문구 없음 → 0.7/0.4% 단일 수치와 '1천만 원 이하만 카드 납부' 삭제·수정, 납부 경로(홈택스·카드로택스·CD/ATM·세무서) 안내, 참고 자료 추가
- 80 청년미래적금: 2026년 1차 가입 신청 6.22~7.3(금융위 보도자료 제목·기사), 다음 모집은 공지 확인 문장 추가. 세부 기여금 조건은 공식 원문 미대조로 그대로
- 그대로 둔 것: 78 '연세액 10만 원 미만은 1·3월만 연납' — 10만 원 이하는 6월 1기분에 전액 부과(지방세법 128조)라 앞당길 수 있는 달이 1·3월뿐인 것과 맞음. 35·68 '반기신청자의 자녀장려금은 하반기분 지급 때 함께' — 국세청 보도 내용(세정일보)과 일치
- 백업: ~/backups/2026-11-06-drafts-3/wp_posts-73-80.sql

## 2026-11-06 승인 대기 7편 쇼츠 대본 점검(수정 안 함, 사용자 결정 3번 '그대로 두기')
- 76 장면3 "총급여 5,400만 원 이하"는 틀림(5,500만 원) / 82 비자발적 이직 요건 없음 → 유튜브에 올리지 않거나 올릴 때 사용자가 직접 확인
- 78 1월 연납 4.6%는 2026년 기준(2027 미발표), 80 가구 중위소득 200% 조건 빠짐 — 오해 소지 정도
- 70 유튜브 설명란에 원래 원고의 '서울특별시 공고 기준'이 그대로 나감(build_upload_kit이 저장된 reference_note 사용)
- 참고: 텔레그램 [발행]은 워드프레스 임시 글 상태만 바꾸므로(wp_client.publish) 오늘 고친 글 내용이 그대로 발행됨. redo/redraft를 실행하면 저장된 원래 원고로 덮어써져 직접 고친 내용이 사라짐

## 2026-11-06 주제 자동 추천 1단계: 후보 찾기 + 미리보기(전송·등록 없음)
- 지시자: 사용자(계획 승인: 서버 프로그램이 주 1회, 텔레그램 후보 최대 10개 중 고른 것만 등록, 범위 유지, 연도는 새 글·같은 해 변경은 기존 글 고침, [새 후보 더 받기] 버튼) / 작업자: Claude / 검토자: Codex·Gemini(계획 검토, 읽기 전용 — 후보 DB 보관·버튼 64바이트·mark_done 전체 삭제·트렌드 기준어·제도명 기준 중복 등 반영)
- 계획 전문: ~/.claude/plans/proud-watching-candle.md
- 확인: 지금까지 주제 10개(작업 2~11)는 2026-11-04 다른 대화에서 Claude가 후보를 떠올리고 네이버 검색어트렌드·공식 자료 검색으로 고른 것
- 파일: src/topic_finder.py(새로), templates/topic_seeds.txt(기본 제도 44개, data/는 Git 제외라 templates에 둠), scripts/find_topics.py(--dry-run만), tests/test_topic_finder.py(18개)
- 동작: 기본 목록 + 최근 3주 뉴스 제목에서 Gemini가 고른 새 제도 → 쓴 글 장부(작업 장부에서 제도명·연도 추출, 취소 제외)와 비교 → 새 주제/새 연도 글(해가 바뀐 뒤 + 공식 자료 제목에 그 연도)/기존 글 고치기(글 쓴 뒤 변경 뉴스 2건 이상, 주장·검토·지역 기사 제외) → 공식 자료 1곳 이상 필수 → 관심도(자녀장려금 기간 평균=100) + 제철 + 새 제도 가점, 분야별 7개·고치기 3개 상한 → Gemini 주제 문장(지어낸 숫자 있으면 틀 문장)
- 실제 검증: pytest 292 통과. 실제 실행 1분 40초, 네이버 호출 약 95번, Gemini 2번. 결과 10개 모두 안 쓴 제도(새출발기금·국가장학금·노인일자리·스포츠강좌이용권·내일배움카드·K-패스·청년문화예술패스·국민취업지원제도·육아휴직급여·양도소득세)
- 고친 것(실제 실행에서 발견): 11월에 '2027년' 계획 기사로 막 쓴 글에 새 연도 후보가 붙음 → 해가 바뀐 뒤에만. "종부세 완화 필요"(주장 기사)·"부산시 소상공인 에너지바우처"(지역 사업)가 변경 근거로 잡힘 → 주장·지역 기사 제외
- 남은 일: 2단계(topics DB·텔레그램 후보 메시지·[번호] 등록 버튼·[새 후보 더 받기], Codex 검토), 3단계(실행기 주 1회 연결, 재시작은 사용자 승인), 이후 새 연도 글 발행 시 옛 글 안내 링크

## 2026-11-06 주제 자동 추천 2단계: 텔레그램 후보 메시지·등록 버튼(실행기 재시작 전)
- 지시자: 사용자("1") / 작업자: Claude / 검토자: Codex(4차 PASS, REVIEW.md)
- 파일: src/topic_store.py(새로, data/topics.sqlite3 — 작업 장부와 분리), src/topic_service.py(새로: 회차 실행·등록·더 받기), src/telegram_notifier.py(후보 메시지·t:/m: 서명 버튼·누른 버튼 하나만 '등록됨(작업 N)'으로 바꾸기), src/orchestrator.py(버튼 종류별로 나눠 처리), scripts/find_topics.py(--send), tests/test_topic_service.py
- 동작: 메시지 1개에 후보 최대 10개(종류·분야·관심도·이유·공식 자료·뉴스·경고), [n번 등록] 2열(고치기 후보는 버튼 없이 원래 글 링크), 맨 아래 [새 후보 더 받기(오늘 n번 남음)]. 등록은 db.create_job(source='web', source_ref='topic:후보번호'), 주제 문장은 후보 장부에서 읽음. 후보 7일 만료, 안 고른 후보 21일 쉼, 같은 주제를 다른 메시지에서 이미 등록했으면 작업 장부로 확인해 거절
- 안전장치: 더 받기는 잠금 + BEGIN IMMEDIATE로 하루 3회 예약 후 별도 스레드(승인 버튼 계속 동작), 주간 회차는 주차 고유 색인, 전송 직전 sending 기록(멈추면 sent로 보고 재전송 안 함), 보내기 전 멈춤은 failed로 재시도 가능, 긴 메시지는 후보 단위로 생략
- 검증: pytest 310 통과. 실제 텔레그램 전송은 아직 안 함 — 지금 돌고 있는 실행기는 옛 코드라 새 버튼을 '처리할 수 없는 요청'으로 버림. 실행기 재시작 후 --send로 실제 확인 필요(사용자 승인 대기)
- 남은 일: 실행기 재시작 + 실제 전송·버튼 확인, 3단계(주 1회 자동 실행: 월요일 오전 한국 시간, 실패 시 다음 날 1회 재시도)

## 2026-11-06 주제 추천 실제 시험 + 국가장학금 글(작업 12) 검토·보류
- 지시자: 사용자(재시작 1번, 검토 요청, 보류 1번) / 작업자: Claude / 검토자: 없음(글 검토, 코드 변경 없음)
- 실행기 재시작 13:44 UTC(진행 중 작업 없음 확인) → `find_topics.py --send`로 실제 후보 10개 전송(회차 1, 메시지 93). 사용자가 1번 등록 → 작업 12번 생성, 버튼 '등록됨(작업 12)'로 바뀜, 같은 버튼 두 번 더 눌러도 작업 1건(중복 방지 실제 확인)
- 작은 문제: 이미 등록된 버튼을 다시 누르면 같은 글자로 바꾸려다 텔레그램 400 'message is not modified' 경고(동작 영향 없음) → 3단계에서 정리
- 작업 12 검토(임시 글 105): 숫자는 출처와 일치(다자녀 610·505·465·135만, 셋째 이상 전액 — 한국장학재단 보도자료·정책브리핑으로도 2026년 기준 확인). 문제: (1) 2027년 1학기부터 학자금 지원구간 10구간→가~마 5구간 개편(한국장학재단 보도자료 seqNo=20927)이 글·쇼츠에 없음 (2) 신청 시기 없음(2학기 2차 9.9. 마감, 다음은 2027학년도 1학기 1차 — 작년 11.20.~12.26., 올해 일정 미발표) (3) 제목은 '지원 금액'인데 Ⅰ유형 금액표 없음 (4) 참고 자료 제목이 모두 '한국장학재단'
- 결정(사용자 1번): 발행하지 않고 보류. 2027학년도 1학기 공고가 나오면 topic에서 '2026년'을 빼고 `jobctl redo 12`(같은 임시 글 유지). 확인 예약: 2026-11-16 10:13 KST(send_later trig_01LjFQXpsBZc4xLG5cB1DArA)
- 3단계에 추가할 것: 다음 해에 신청이 열리는 제도는 주제 문장에서 연도 빼기, 연말에 '다음 해·다음 학기 개편 예정' 뉴스가 있는 주제는 '개편 예정, 공고 뒤 쓰기 권장' 표시, 등록된 버튼 재클릭 시 버튼 수정 생략

## 2026-11-06 주제 자동 추천 3단계: 주 1회 자동 실행(실행기 재시작 전)
- 지시자: 사용자("1") / 작업자: Claude / 검토자: Codex(2차 PASS, REVIEW.md)
- 변경: src/orchestrator.py run_forever에 'topic-weekly' 스레드(10분마다 확인), src/topic_service.py run_weekly_if_due(한국 시간 월요일 9시 이후 그 주 회차 없으면 실행, 월요일에 꺼져 있었으면 그 주 안에 따라잡음, 실패 시 텔레그램 안내 + 20시간 뒤 1회만 재시도, [더 받기] 실행 중이면 실패로 세지 않고 다음 확인 때), src/topic_finder.py(다음 해 표현 + 개편·변경 말이 제목·요약에 함께 있으면 '개편 예정, 공고 뒤 쓰기 권장' 경고·점수 -1, 11월부터 새 주제 문장에서 연도 제거), 등록된 후보 버튼 재클릭 시 버튼이 이미 바뀌었으면 수정 생략(텔레그램 400 경고 해결)
- 검증: pytest 321 통과
- 남은 일: 실행기 재시작(사용자 승인) → 첫 자동 발송 2026-11-09(월) 09:00~09:10 KST 확인
- 실행기 재시작: 사용자 승인("1") 후 2026-11-06 14:42 UTC, active·스레드 3개(작업·버튼 수신·주간 주제) 확인
- 주의(처리함): 이번 주(2026-W45)는 주간 회차 기록이 없어 재시작 10분 뒤 첫 확인 때 금요일 밤에 한 번 더 보낼 상황이었음 → 첫 확인 전에 W45 weekly 회차를 'sent'(메모: 11-06 수동 발송으로 대신)로 기록, weekly_due False 확인. 첫 자동 발송은 2026-11-09(월) 09:00~09:10 KST(W46)

## 2026-11-07 블로그 대표 사진 중복 방지 + 겹친 대표 사진 4개 교체
- 지시자: 사용자(글 목록에 같은 썸네일이 보여 헷갈림 → "1": 앞으로 막고 기존 겹친 글도 교체) / 작업자: Claude / 검토자: Codex(REVIEW.md)
- 원인: 사진 중복 방지가 한 글 안에서만 동작, 세금·지원금 글은 검색어가 비슷해 검색 1등 사진(계산기 1044173 등)이 반복
- 변경: src/db.py used_blog_media_ids(다른 작업 체크포인트의 블로그 사진 번호), src/stock_media.py(avoid로 다른 글 사진 먼저 피하고 없으면 재사용, 블로그 사진은 번호 차 20 이내 연작도 피함 — 1044172/1044173처럼 같은 작가의 거의 같은 사진), src/pipeline.py·orchestrator.py(avoid 전달), src/wp_client.py replace_featured, scripts/fix_duplicate_thumbnails.py(기본 미리보기, --apply 실행)
- 실제 실행: 백업 data/backups/thumbs-20261107-095010(jobs.sqlite3, wp_posts·wp_postmeta 덤프, 옛 jcjob-3/8/11/12 파일) 후 --apply. 글57(발행) 56→108, 글76 74→110, 글82 81→112, 글105 103→114(임시 글). 옛 첨부 4개 삭제
- 검증: pytest 328 통과, 재실행 시 '겹치는 글 없음', find_media(jcjob-N-i)가 새 첨부를 찾음(redraft 해도 새 사진), 홈페이지 200·jcjob-3-1-1 표시. 화면 캡처는 서버에 Playwright 브라우저가 없어 생략
- 참고: 작업 1 본문 사진(1867758)과 작업 10 대표 사진은 같음(목록 화면엔 대표 사진만 보여 그대로 둠). 실행기 재시작 필요(새 글 중복 방지는 재시작 후 적용) — 사용자 승인 대기

## 2026-11-08 실행기 재시작 + 검색엔진 차단 해제
- 지시자: 사용자("1": 재시작 + 차단 해제. 썸네일은 제목 글자 없이 일반 사진 유지로 정리) / 작업자: Claude / 검토자: 없음(설정 1개 변경, 코드 변경 없음)
- 실행기: 대기·진행 작업 0건 확인 후 2026-11-08 01:08 UTC 재시작, active·'실행기 시작' 로그 확인 → 새 글 대표 사진 중복 방지 적용
- 검색엔진: 공개 글 8개·소개·문의·개인정보 페이지 채워진 것 확인 후 wp_options blog_public 0→1(백업 data/backups/blog-public-*/blog_public.sql). 글·홈 robots 메타 'noindex, nofollow' → 'max-image-preview:large', wp-sitemap.xml 200(글 8개). 원 서버 robots.txt에 Sitemap 줄 생김(Cloudflare가 robots.txt를 최대 4시간 캐시해 그 뒤 반영)
- 남은 일: 구글 서치 콘솔 등록(사용자 구글 계정 필요, 소유 확인은 DNS TXT 또는 HTML 태그) → 사이트 지도 https://jalcheck.com/wp-sitemap.xml 제출. 사진 파일 이름을 내용 있는 이름으로(선택, 효과 작음)
- 2026-11-08 추가: 구글 서치 콘솔 HTML 태그 소유 확인 줄을 deploy/wp/mu-plugins/jalcheck-seo.php에 추가(홈 화면에만, SEO 끄기 설정과 무관하게 출력) → 서버 mu-plugins에 복사(원본 백업 data/backups/gsc-*). 홈 200·태그 1개 확인, 글 페이지 200. 남은 일: 사용자가 서치 콘솔에서 [확인] → Sitemaps에 wp-sitemap.xml 제출
- 2026-11-08 추가(사용자 "1"): jalcheck-seo.php에 태그·글쓴이 목록 페이지 noindex, follow + 사이트 지도에서 태그·사용자 제외(태그 28개 대부분 글 1개). 백업 data/backups/seo-tags-*. 확인: 홈·글·분류 200 색인 유지, 태그·글쓴이 noindex, follow, 사이트 지도 글·페이지·분류 3개만, 태그·사용자 사이트 지도 404. 남은 선택: 네이버 서치어드바이저·다음 등록(사용자 확인 태그 필요), 빙은 서치 콘솔에서 가져오기
- 2026-11-08 추가: 네이버 서치어드바이저 HTML 태그 소유 확인 줄을 jalcheck-seo.php 홈 화면에 추가(백업 data/backups/naver-*). 홈 200, 구글·네이버 태그 모두 출력, 네이버 로봇(Yeti) 접속 200. 남은 일: 사용자가 [소유확인] → 요청 > 사이트맵 제출(https://jalcheck.com/wp-sitemap.xml)

## 2026-11-08 카테고리 개편: 지원금·세금 1개 → 세금·절세 / 지원금·복지 / 청년·일자리
- 지시자: 사용자("ㄱ", 확장 범위 1번 '생활 제도·돈 정보 전반') / 작업자: Claude / 검토자: 없음(분류 이름·글 분류 지정·주소 이동 1개, 로그인·결제·DB 구조 변경 없음)
- 백업: data/backups/categories-20261108-013623(terms·term_taxonomy·term_relationships·termmeta, blogdescription·default_category, 머리말·홈 틀 49·88, 옛 jalcheck-seo.php), 추가 CSS는 ~/backups/2026-11-08-site-css/
- 분류(REST): 1번 '지원금·세금'(support-tax) → '지원금·복지'(benefits, 기본 분류 유지), 48 '세금·절세'(tax), 49 '청년·일자리'(youth-jobs), 각 분류 설명문(분류 화면·검색 설명문에 쓰임)
- 글 배정: 세금·절세 55·76·57·73·78 / 지원금·복지 31·68·35·70·59 / 청년·일자리 80·82·105(셋 다 임시 글). 글 주소는 /%postname%/라 그대로
- 사이트 설명: "세금·지원금·주거·금융, 공식 자료로 확인한 생활 제도 체크리스트"
- 파일: deploy/wp/parts/header.html(메뉴: 세금·절세 / 지원금·복지, 소개·문의는 꼬리말에만), deploy/wp/templates/home.html(제목 문구, 옛 '전체 보기' 링크 삭제), deploy/wp/jc-site.css(메뉴 글자 줄바꿈 금지, 640px 이하에서 1줄 로고·검색 / 2줄 분류 메뉴, 검색창 열면 첫 줄만 덮음), deploy/wp/mu-plugins/jalcheck-seo.php(옛 분류 주소 301: /category/support-tax/… → /category/benefits/…, 404일 때만)
- 검증: 홈·세 분류·소개·글 200, /category/support-tax/ 및 /page/2/ → 301 benefits, 분류 사이트 지도에 benefits·tax(글 없는 youth-jobs는 자동 제외), 분류 설명문이 meta description으로 출력. 화면 캡처(tools/pw-browsers) 320·360·390·1280px 가로 넘침 없음, 검색창 열기 확인
- 남은 일: 청년·일자리 글을 처음 발행할 때 header.html에 '청년·일자리' 메뉴 추가 후 apply_site_pages.py(지금은 빈 분류라 메뉴에서 뺌). 자동 발행 글은 기본 분류(지원금·복지)로 들어가므로 세금·청년 글은 발행 때 분류를 바꾸거나, 파이프라인이 분류를 고르게 하는 개선 필요. 주거·부동산 / 금융·저축 / 건강·의료 / 연금·노후는 글 3개쯤 모이면 추가

## 2026-11-08 작업 10(임시 글 80) 발행 전 검토·본문 수정
- 지시자: 사용자("1": 검토 결과대로 초안 수정) / 작업자: Claude / 검토자: 없음(글 본문 수정만, 코드·DB 구조 변경 없음)
- 원인: 6월 1차 모집 기준으로 쓰여 2026-09-16 금융위 2차 모집 보도자료(정책브리핑 newsId=156781949, 수집된 S1은 첨부 PDF 본문이 빠져 있었음) 내용이 빠짐 → "갈아타기는 6월에만" 오류, 마무리 "다음 모집 확인" 문구가 현재와 안 맞음, 우대형 대상(신규 취업자·연매출 1억 이하 소상공인) 누락
- 수정(REST로 글 80 content 갱신, 상태 draft 유지): 도입부에 2차 모집 요약, 갈아타기 2차 재허용·순서·주의(계좌 개설 전 해지 금지), 새 소제목 '2차 모집 일정'(신청 10.7~16, 심사 10.19~11.13, 계좌 개설 11.16~27, 3차 미정)과 표, 맞벌이 2인 가구 250%, 우대형 세 갈래, 일반형 종합소득 4,800만 원, 2027 예산안(우대형 15%·지방 25%, 소급) '확정 전' 상자, 참고 자료에 정책브리핑 2차 보도자료 추가, 확인일 11-08
- 백업: data/backups/posts/post80-20261108-014746.html
- 검증: 수정 후 REST raw content가 새 본문과 동일, HTML 태그 짝 맞음, 본문 약 3,260자(공백·태그 제외)
- 남은 일: 사용자 발행 승인. 글 80은 '청년·일자리' 분류 첫 공개 글이 되므로 발행 시 header.html 메뉴에 '청년·일자리' 추가 필요. 개선 과제: 출처 수집 시 보도자료 첨부 PDF 본문도 읽기(S1처럼 껍데기만 수집되는 문제)

## 2026-11-08 자동 발행 글 분류(카테고리) 자동 선택(실행기 재시작 전)
- 지시자: 사용자("진행해줘") / 작업자: Claude / 검토자: Codex(2차 PASS, REVIEW.md)
- 이유: 카테고리 개편 뒤에도 자동 발행 글은 모두 기본 분류(지원금·복지)로 들어감
- 변경: src/wp_client.py(list_categories — 빈 분류 포함, save_draft categories), src/ai_generator.py(choose_category: 사이트 분류 목록의 slug·이름·설명을 주고 하나 고르게 함, 목록 밖이면 오류), src/pipeline.py(make_draft에서 분류 선택, 실패 시 분류를 보내지 않고 승인 '확인할 것'에 경고 — 새 글은 기본 분류, 기존 임시 글은 원래 분류 유지), src/orchestrator.py(draft 체크포인트의 경고·분류 이름을 승인 요청에 전달, 옛 체크포인트 호환), src/telegram_notifier.py(승인 메시지에 '분류: …' 줄)
- 분류는 코드에 적지 않고 매번 워드프레스에서 읽음 → 관리자 화면에서 분류를 추가하고 설명을 적으면 다음 글부터 자동으로 후보가 됨(설명이 판단 근거라 분명하게 적을 것)
- 검증: pytest 336 통과(새 테스트: 분류 선택·실패 경고·옛 체크포인트 재개·저장된 경고 재개·기존 글 실패 시 분류 미전송·목록 밖 slug 거절·텔레그램 이스케이프·REST 분류 목록), 실제 Gemini로 기존 글 13개 미리 시험 13/13 일치(저장 안 함)
- 남은 일: 실행기 재시작(사용자 승인) 후 다음 자동 글에서 승인 메시지 '분류:' 줄과 워드프레스 분류 확인. 청년·일자리 첫 발행 때 머리말 메뉴 추가
- 2026-11-09 예약 확인: 11-08 01:15 UTC 이후 Googlebot의 wp-sitemap 요청은 아직 없음. 대신 Googlebot이 robots.txt 3회·대표 사진 9장·태그 페이지 1개를 모두 200으로 가져감(Cloudflare 차단 없음). 네이버 Yeti는 /feed/ 200. sitemap 200 정상. 서치 콘솔 '가져올 수 없음'은 첫 수집 전 표시로 판단, 며칠 더 지켜보기
- 실행기 재시작: 사용자 승인("1") 후 2026-11-09 10:55 UTC(진행 작업 0건, 승인 대기 3건, 이번 주 W46 주제 추천은 00:08 UTC 발송 완료 확인). active·'실행기 시작' 로그 확인 → 다음 새 글부터 분류 자동 선택 적용

## 2026-11-09 머리말 메뉴에 '청년·일자리' 추가
- 지시자: 사용자("발행했나? 확인하고 추가") / 작업자: Claude / 검토자: 없음(메뉴 링크 1개)
- 확인: 글 80(청년도약계좌·청년미래적금)이 2026-11-09 19:55 KST 발행 → 청년·일자리 첫 공개 글. 82·105는 아직 임시 글
- 변경: deploy/wp/parts/header.html에 청년·일자리(/category/youth-jobs/) 추가 후 사이트에 저장(백업 data/backups/menu-youth/part-header.html)
- 검증: 홈·분류·글 200, 메뉴 링크 3개, 분류 사이트 지도에 youth-jobs 자동 포함, 화면 캡처 320px(메뉴 한 줄에 다 들어감, 가로 넘침 없음)·1280px

## 2026-11-09 연금저축 쇼츠 수정본, 국가장학금 예약 재설정, 서버 IP 가리기, 로그인 제한
- 지시자: 사용자("1") / 작업자: Claude / 검토자: Codex(nginx 변경 2차 PASS, REVIEW.md)
- 쇼츠: 작업 8 장면 3의 '총급여 5,400만 원'을 5,500만 원으로 고쳐 data/jobs/8/short-fixed/short.mp4 새로 제작(같은 배경·목소리, 다른 장면 그대로), 텔레그램으로 영상·제목·설명 전송. 작업 장부(checkpoint)와 글은 안 바꿈. 원본 data/jobs/8/short/ 유지. 유튜브 교체는 사용자가 직접
- 예약: 국가장학금 11-16 10:13 KST 확인을 닫힌 대화에 묶인 trig_01LjFQXp...에서 실행 때마다 새 대화를 여는 trig_01StBKmtsbAdvLa7n2aqgaiE로 교체(같은 방식의 옛 예약 2건이 auto_disabled_session_gone으로 실패했음)
- 서버 IP: wp_users 1번 user_url → https://jalcheck.com, wp_posts guid 4건(공개 페이지·내비게이션·휴지통) http://168.110.20.97 → https://jalcheck.com. 백업 ~/backups/2026-11-09-hide-ip/. 공개 API(users·pages)에 IP 0건 확인
- nginx: deploy/nginx/cloudflare-realip.conf(새로, /etc/nginx/conf.d/) — CF-Connecting-IP로 실제 방문자 IP 복원, wp-login 제한 영역 / deploy/nginx/default.conf — wp-login.php IP당 평균 12초 1회·순간 약 11회(초과 429), xmlrpc.php 차단. 백업 ~/backups/2026-11-09-nginx/. 되돌리기: 백업 default.orig를 /etc/nginx/sites-available/default로 복사, conf.d 파일 삭제, nginx -t 후 reload
- 검증: nginx -t 통과, 홈·소개·문의·글·사이트 지도·/wp-admin/(302)·/wp-json/ 정상, xmlrpc POST 403, wp-login 16회 연속 → 200 11회 후 429 5회, 접속 기록에 Cloudflare IP 대신 실제 IP, 자동 발행용 REST 로그인 조회 정상. 실행기 active
- 미검증·한계: 관리자 화면 실제 로그인은 사용자 비밀번호가 필요해 미시험(로그인 화면 200까지 확인). 여러 IP로 나눈 공격·REST 인증 공격은 못 막음
- 남은 일: (1) 자동 백업 — 사용자에게 계획 보고함, 바깥 보관 방식 결정 대기 (2) 서버 직접 접속을 Cloudflare만 허용 — 사이트 전체에 영향이라 별도 승인 후 (3) 연말정산 글 44%·33%·기본공제 요건 공식 확인 (4) 임시 글 31번(작업 900004 시험 글, 장부에 없음) 정리 (5) 글을 직접 고친 뒤 쇼츠 불일치 경고 (6) 에너지바우처 쇼츠 설명란 문구 (7) 사용자: 실업급여 승인 시 쇼츠 그대로 올리지 않기, 청년미래적금 쇼츠 가구 소득 조건 보완

## 2026-11-09 자동 백업(암호화, 매일 새벽 4시 KST)
- 지시자: 사용자("1": GitHub 비공개 저장소) / 작업자: Claude / 검토자: Codex(2차 PASS, REVIEW.md)
- 이유: 글 DB·작업 장부 자동 백업이 없었고 수동 백업도 서버 안에만 있었음
- 파일: scripts/backup_site.py, deploy/systemd/jalcheck-backup.service·timer, tests/test_backup_site.py, .env.example(BACKUP_GIT_REMOTE)
- 담는 것: wordpress DB, uploads, mu-plugins, jobs·topics 장부, wp-config, .env, ~/.wp_db_pass, nginx 설정, Cloudflare 원본 인증서·키, 실행기 unit. 쇼츠 영상·스톡 재료 제외. gpg AES256(암호 ~/.backup_pass, 백업에 미포함)
- 보관: ~/backups/auto 최근 14개 + 월별 6개. 바깥은 최근 7개 + 월별 3개를 새 기록 하나로 강제 덮어쓰기(저장소 주소 전체를 코드에 고정). 암호화 전 임시본은 /dev/shm. 실패 시 텔레그램 알림
- 검증: 임시 DB jc_restore_test 복원(표 건수 일치, 글 103건 본문·상태 일치, 복원 후 삭제), 장부 복원·무결성 ok, wp-config·.env·인증서 키·nginx 해시 일치, 사진 93개 일치, 틀린 암호 거부, 강제 종료 잔여물 정리, systemd 실행 성공, pytest 전체 통과
- 남은 일(사용자): GitHub에 비공개 저장소 bigapple117/jalcheck-backup 만들고 deploy key(~/.ssh/jalcheck_backup_ed25519.pub, 쓰기 허용) 등록 → Claude가 .env에 BACKUP_GIT_REMOTE=git@github-backup:bigapple117/jalcheck-backup.git 넣고 첫 업로드·GitHub에서 내려받아 복원 시험 → 복원 암호(~/.backup_pass)를 서버 밖에 보관. 암호 전달 방식은 사용자 확인 후
- 2026-11-10 GitHub 바깥 보관 연결: 사용자가 비공개 저장소 bigapple117/jalcheck-backup 생성·deploy key(쓰기) 등록 → .env에 BACKUP_GIT_REMOTE 추가 → systemd로 실행해 '바깥 보관 완료'. 확인: 로그인 없이 저장소 주소 404(비공개), 원격 기록 1개·파일 5개, GitHub에서 내려받은 최신본이 서버 최신본과 해시 동일, 그 파일을 풀어 임시 DB 복원(wp_posts 134·wp_users·wp_options 건수 일치, 복원 후 삭제). 11-10 04:03 KST 첫 자동 백업 성공. 크기 3.8→8.2MB는 새 글·사진 증가(작업 13~22) 때문
- 남은 일: 복원 암호(~/.backup_pass)를 서버 밖에 보관 — 전달 방식 사용자 확인 대기
- 2026-11-10 복원 암호를 사용자 텔레그램으로 1회 전송(사용자 '1' 승인, 대화·로그에는 미출력). 메시지 번호 data/.backup_pass_message.json — 사용자가 저장 확인하면 deleteMessage로 삭제(봇은 보낸 지 48시간 안에만 지울 수 있음)
- 2026-11-10 사용자가 복원 암호 저장 확인 → 텔레그램 암호 메시지 2개 삭제(deleteMessage True). 자동 백업 작업 완료

## 2026-11-10 연말정산(55)·고향사랑(57) 글 미확인 수치 공식 확인(수정 없음)
- 지시자: 사용자("1") / 작업자: Claude / 검토자: 없음(조사만, 글·코드 변경 없음)
- 고향사랑기부금 2026년 기부분: 10만 원 이하 100%, 10만~20만 원 44%(국세 40%+지방세), 20만 원 초과 16.5%(국세 15%), 특별재난지역은 20만 원 초과분 33%·선포일부터 3개월 내 기부·2025 과세기간부터, 연 2,000만 원 한도 → 55·57번 글 수치·계산 예시(20만 원 14.4만, 100만 원 27.6만) 모두 맞음. 근거: 정책브리핑 148969870(2026.08.13, 원문), 행정안전부 고향사랑기부제 안내 페이지(본문 추출)
- 부양가족 기본공제 소득요건: 현행 소득금액 100만 원(근로소득만 총급여 500만 원) 맞음. 다만 2026 세제개편안(8.3 발표)에 300만 원·750만 원으로 완화안 — 적용 시기 기사에 없음, 정부안이라 국회 심의에서 달라질 수 있음(148969870)
- 개편안: 고향사랑기부금 지역 차등(비수도권 우대지역 10만~20만 55%, 20만 초과 27.5%, 지방세 포함) '27.1.1.부터(행정안전부 148969910, 2026.08.12)
- 남은 일: 55번에 소득요건 개편안 '확정 전' 안내, 57번에 2027년 지역 차등 개편안 안내 추가 여부(사용자 결정), 12월 국회 의결 후 확정 내용·적용 시기 확인
- 2026-11-10 이어서(사용자 "1"): 55번에 '확정 전' 상자(소득요건 300만·750만 완화안, 국회 심의 중, 현재 100만 기준으로 안내)와 참고 자료 148969870, 57번 계산 예시 아래 '확정 전' 상자(2027.1.1. 기부분부터 지역별 최대 55%·27.5%, 2026 기부분은 표 그대로)와 참고 자료(행정안전부 안내, 148969910). REST로 수정, 상태 publish 유지, 자료 확인일은 그대로(글 전체 재검토 아님). 백업 ~/backups/2026-11-10-posts55-57/. 확인: 두 글 200·상자·출처 출력, 390px 가로 넘침 없음·캡처 확인. 국회 의결 확인 예약 2026-12-11 10:17 KST(실행 때마다 새 대화)

## 2026-11-10 시험 임시 글 31번 휴지통으로
- 지시자: 사용자("2") / 작업자: Claude / 검토자: 없음(임시 글 1개 휴지통, 되돌릴 수 있음)
- 대상: 31 "2026년 근로장려금 신청 자격과 기한 후 신청 방법"(2026-11-02 시험 작업 900004, 작업 장부에 없음, 하반기 근로장려금 글 68과 주제 겹침). 다른 글 링크·장부·화면 캡처 틀(공개 글만 사용) 어디서도 안 씀 확인
- REST DELETE(force 없음) → trash. 대표 사진 첨부 32(jcjob-900004-4.jpg)는 다른 곳에서 안 쓰지만 그대로 둠(작음)
- 백업 ~/backups/2026-11-10-post31/(wp_posts 31·리비전 33, postmeta, 분류 연결). 되돌리기: 관리자 화면 휴지통에서 복원(워드프레스 기본 30일 뒤 자동 비움) 또는 백업 SQL
- 확인: 홈·지원금·복지 분류 200, 글 주소는 공개된 적 없어 404

## 2026-11-10 쇼츠 개선 시안(작업 22 장애인연금) — 자동 제작은 안 바꿈
- 지시: 사용자("AI 티 나고 재미없음" → 1번 '시안 하나 만들어 비교') / 작업: Claude / 검토: 없음(로컬 시안, 운영 반영 없음)
- 진단: 대본이 블로그 요약문(첫 문장 "정리해 드립니다", 매번 같은 순서, 끝이 "블로그에서 확인"), 배경이 주제와 무관(장애인연금에 담배 피우는 손 — 검색어 'hands holding papers'), 화면 30% 어둡게, 자막 강조 없음, 배경음악·효과음 없음
- 시안: data/jobs/22/short-v2/short.mp4(34초, 렌더 약 2분 30초). 대본은 Claude가 글(작업 22) 사실만으로 다시 씀. 정보 카드·체크 목록(ASS 그림), 숫자 노란 강조 자막, 위쪽 진행 막대, 머리 제목 상자(사이트 색 #0f6b74), 배경음악 Kevin MacLeod "Daily Beetle"(incompetech, CC BY 4.0 — 올릴 때 설명란 출처 표기 필요, 목소리 나올 때 sidechain으로 줄임), 효과음은 ffmpeg로 합성, 성우 같고 속도 +10%
- 제작 스크립트는 시제품이라 프로젝트 밖(세션 임시 폴더)에 둠. 반영 결정 시 tts_engine/video_renderer/prompt_template에 정식 구현 필요
- 실제 검증: 프레임 캡처로 카드·자막·제목 위치 확인, 소리 크기 -16.9 LUFS, 텔레그램으로 기존·시안 영상 전송
- 남은 일: 사용자 평가 대기 → 좋으면 (1) 대본 규칙 지시문 반영 (2) AI가 장면별 카드 문구를 함께 내도록 형식 추가 (3) 렌더러에 카드·강조·음악 (4) 배경 영상 관련성(담배·술·외국 지폐 거르기) (5) 목소리 교체 실험

## 2026-11-10 쇼츠·글 숫자 불일치 경고(실행기 재시작 전)
- 지시자: 사용자("1") / 작업자: Claude / 검토자: Codex(3차 PASS, REVIEW.md)
- 이유: 승인 전에 글을 직접 고쳐도 쇼츠는 옛 내용 그대로라 틀린 영상이 올라감(작업 8 '총급여 5,400만')
- 변경: src/youtube_kit.py(amounts: 억·천만·만·천 단위 읽기·결합, numbers_missing_from_post: 쇼츠 제목·대사·유튜브 설명·정보 기준의 숫자 중 현재 글 본문에 없는 것. 단위 금액은 원 단위 값으로 비교하고 버림·반올림은 차이 10% 미만일 때만 같은 값, 단위 없는 정수는 값으로, 소수는 표기로, 한 자리 정수 제외), src/wp_client.py(get_post_content), src/orchestrator.py(shorts_number_check: 승인 요청 '확인할 것'에 경고, 발행 후 업로드 안내 때 영상보다 먼저 '올리기 전 확인 필요' 메시지. 확인·전송 실패는 기록만 하고 흐름 계속)
- 검증: pytest 363 통과. 실제 글 21편(쇼츠 있는 작업 전부)에 적용 → 작업 8 '장면 3: 5,400만'만 잡힘(실제 오류), 오탐 0. 처음 버전은 주거급여(약 311만↔3,117,474원, 36만 9천↔369,000원)·산모신생아(152만↔1,525,000원) 표기 차이를 오탐 → 값 비교로 해결
- 한계: 숫자만 비교(문장 내용·'서울특별시' 같은 글자 차이는 못 잡음), 경고만 하고 막지 않음
- 남은 일: 실행기 재시작(사용자 승인) 후 적용. 승인 대기 6편(작업 17~22)은 이미 승인 요청을 보냈으므로 발행 시 업로드 안내 단계에서만 확인됨
- 실행기 재시작: 사용자 승인("1") 후 2026-11-10 07:58 UTC(진행 작업 0건, 승인 대기 7편), active·'실행기 시작' 로그 확인 → 숫자 대조 경고 적용
- 추가(같은 날): 사용자 요청으로 마지막 홍보 화면 대신 제목 상자 아래 작은 반투명 "잘체크 · jalcheck.com" 상시 표시(시청 이탈 없음). 시안 2 텔레그램 전송

## 2026-11-10 서버 직접 접속 차단 보류 + 업데이트 상태 점검(변경 없음)
- 지시자: 사용자 / 작업자: Claude / 검토자: 없음(조사만)
- 직접 접속 차단: 계획·부작용 보고 후 사용자와 보류로 정함(개인 블로그엔 필수 아님, 수익·방문자 증가나 공격 증가 시 재검토, 하게 되면 nginx Cloudflare 대역만 허용 + 목록 매일 자동 갱신). 참고: 서버 IP 직접 접속은 11-04~08 하루 약 800~2,400건, 대부분 .env/.git/로그인 탐색 스캐너
- 업데이트 점검: 워드프레스 7.1.4(최신, 메이저·마이너 자동 업데이트 켜짐, 파일 소유 www-data라 자동 업데이트 가능), 활성 플러그인 0개(미사용 akismet 5.7.2·hello.php 설치만), 테마 twentytwentyfive 1.5 사용 중(사이트 틀·스타일은 DB 저장: wp_template 4·part 2·global_styles 1이라 테마 업데이트로 안 덮어씀), 미사용 테마 2023·2024, 플러그인·테마 자동 업데이트 꺼짐(업데이트 대기는 없음). 운영체제 보안 업데이트 매일 자동(11-10 06:20 설치 완료), 재부팅 필요 없음
- 제안(사용자 결정 대기): 미사용 플러그인 2개·테마 2개 삭제, 테마 자동 업데이트 켜기
- 목소리 비교(같은 날): edge-tts 3종(SunHi·InJoon·HyunsuMultilingual, +10%)과 Gemini gemini-3.8-flash-tts 4종(Kore·Aoede·Puck·Charon) 견본을 텔레그램으로 전송, 사용자 선택 대기. 시험 결과: Gemini TTS는 문장 앞에 말투 지시(한국어·영어 모두)를 붙이면 지시문까지 소리 내 읽는 경우가 있었고, 아라비아 숫자(34만 9,700원)를 33만·334,700처럼 다르게 읽은 것으로 받아쓰기됨. 지시 없이 숫자를 한글로(삼십사만 구천칠백 원) 넣으면 4종 모두 받아쓰기와 일치. 도입 시 숫자 한글 변환 + 받아쓰기 대조 검사 + 장면별 시간은 받아쓰기/비율로 맞추는 처리 필요(단어 시간 정보 없음). 무료 사용 한도는 미확인
- 2026-11-10 이어서(사용자 "1", 검토자 Codex PASS — data/design-review/codex-wp-unused-1.txt): 미사용 플러그인 akismet·hello.php, 테마 twentytwentythree·twentytwentyfour를 삭제 대신 ~/backups/2026-11-10-wp-unused/(700, 소유권 www-data 유지)로 이동. 테마 자동 업데이트 켬(auto_update_themes = [twentytwentyfive], update_option). 확인: 홈·글·분류·소개·사이트 지도 200, /wp-admin/ 302, REST 플러그인 0개·테마 twentytwentyfive만(active), 자동 발행 REST 조회 정상, 오류 로그에 새 오류 없음. 되돌리기: sudo mv로 원래 위치(/var/www/html/wp-content/plugins, themes)로. 참고: 대체 테마가 없어졌지만 활성 테마는 정상이고 백업에서 복원 가능

## 2026-11-10 텔레그램 [다음 승인 대기 글 보기] 버튼
- 지시자: 사용자(승인 대기 글을 위로 스크롤해 찾는 불편, "남은 승인 대기부터") / 작업자: Claude / 검토자: Codex(2차 PASS, REVIEW.md)
- 변경: src/telegram_notifier.py(NextAction, 서명 버튼 n:<작업>:<서명>, next_markup, send_text reply_markup, clear_buttons, 수신 파싱), src/orchestrator.py(업로드 안내 설명 메시지·보류·취소·쇼츠 없는 발행·reconcile 발행 뒤 버튼, handle_next: 방금 작업보다 큰 번호의 승인 대기 글(끝이면 처음으로)을 화면 캡처 새로 찍어 다시 보내고 새 nonce 저장, 예전 메시지 버튼 제거. 같은 버튼 중복 처리 방지, 실패 시 재시도 버튼, 장부 저장 실패 시 새 버튼 제거·예전 nonce 유지), approval_warnings로 경고 계산 공통화
- 검증: pytest 376 통과(테스트 14개 추가)
- 참고: 다시 보내기는 버튼 수신 스레드에서 캡처까지 하므로 그동안(1분 안팎) 다른 버튼 응답이 늦어짐
- 실행기 재시작 2026-11-10 08:21 UTC(진행 작업 0건, 승인 대기 7개: 12·17~22) → 텔레그램에 시작 버튼 메시지(작업 16 다음부터, 메시지 194) 전송
- 목소리 결정(같은 날): 사용자가 남성 제외, Gemini 여성 Erinome(G) 선택. 시안 3 = Erinome로 장면별 합성(숫자는 한글로 읽힘, 앞뒤 무음 자름, 자막·카드 시간은 글자 수 비율 추정 — 단어 시간 정보 없음), 37초, 텔레그램 전송. 시험 중 분당 한도(약 10번/분)에 한 번 걸림, 하루 약 30번 써도 하루 한도는 안 걸림. 받아쓰기 검사가 '삼십사만'을 '삼십삼만'으로 적은 적이 있으나 해당 장면만 다시 들려 확인하면 '삼십사만' — 받아쓰기 자체도 틀릴 수 있어 자동 검사로만 믿기 어려움. 원래 선희 버전 시안은 data/jobs/22/short-v2/short-sunhi.mp4
- 시안 4(같은 날, 사용자 지적 반영): (1) 자막이 "받을 수 있는지, 세 / 가지만 보세요"로 끊김 — Gemini 음성은 단어 정보가 없어 문장부호를 단어에 붙인 채 넘겼고, 그래서 쉼표에서 안 끊고 글자 수로만 나눔. 쉼표·마침표에서 먼저 끊고 긴 구절만 고르게 나누게 고침(자막용 문장은 읽는 문장과 따로라 쉼표를 더 넣어도 목소리에 영향 없음) (2) "치익" 잡음 — Gemini로 받아쓰기·소리 점검: 목소리만 있는 파일에서 장면 이음매마다 '딸깍'(앞뒤 무음 자른 자리), 섞은 파일에서 배경음악 하이햇 '칙칙'과 카드 효과음 '쉭'. 장면 음성 앞뒤 짧게 페이드, 음악 고음 깎고(3.8kHz) 18%로 낮추고 말 사이에 천천히 복귀(release 900ms), 카드 효과음 다시 합성·작게. 재점검에서 목소리 파일 잡음 '없음'. 텔레그램 전송, 사용자 확인 대기
- 시안 5(같은 날): 사용자 "안 나옵니다·보세요 뒤 칙 소리". 목소리·음악·효과음을 따로 뽑아 보니 목소리 장면마다 끝에 0.12초 -5dB 소리. 원인: Gemini 음성 API가 소리 데이터를 이미 WAV 파일(머리말 + 끝에 C2PA 'AI 생성' 출처 정보 약 6KB)로 돌려주는데, 머리말 없는 소리로 보고 WAV로 한 번 더 감싸서 머리말(앞 '틱')과 C2PA 정보(끝 '칙')가 소리로 재생됨. 처리: 받은 데이터가 RIFF면 그대로 WAV로 저장해 ffmpeg가 읽게 함, 저장본 6개는 안쪽 data 덩어리만 꺼내 복구(API 재호출 없음). 앞서 보낸 목소리 견본 A~I와 4~7번에도 같은 잡음이 있었음. 다시 렌더 34초, 목소리 전체에서 튀는 잡음 후보 없음. 정식 반영 시 반드시 이 처리 포함
- 시안 6(같은 날): 사용자 "목소리가 중간에 바뀌는 것 같다" — 같은 Erinome이지만 장면마다 따로 6번 호출해 톤·높낮이·빠르기가 매번 달라짐. 대본 전체를 1번 호출로 읽히고(줄바꿈으로 장면 구분), silencedetect로 쉼을 찾아 글자 수로 예상한 위치 근처의 가장 긴 쉼에서 장면을 자름. 경계 7.19/10.17/13.55/19.03/27.8초, Gemini 받아쓰기 문장 시작 시각과 일치, '목소리 바뀌는 곳 없음', 튀는 잡음 없음. 31초. 쇼츠 1편당 음성 호출 1번

## 2026-11-10 쇼츠 개편 자동 제작 반영 (지시: 사용자 '1' / 작업: Claude / 검토: Codex BLOCKED — 로그인 만료, REVIEW.md)
- 커밋: 11f871f(개편), a7c4245(REVIEW 기록)
- 대본(templates/prompt_template.py, src/content_schema.py): 첫 장면 핵심 숫자+놓치면 안 되는 이유, 핵심 하나, 말하듯, 마지막은 할 일(블로그 안내 제거), Shorts.hook(위쪽 질문, 주제 이름 반복 금지), Scene.card(number/check/warn/info, small·big 12자), 쉼표는 실제 쉬는 자리에만. 기본 길이 45→30초(db·jobctl·생성기·시험 스크립트), 장면 수 target/5
- 카드 숫자 검사(src/ai_generator.py tidy_shorts_cards): 카드 숫자가 그 장면 대사에 없으면 카드만 뺌(AI 재호출 없음), hook 20자 넘으면 비움
- 음성(src/gemini_tts.py, src/pipeline.make_tts): 기본 Gemini gemini-3.8-flash-tts·Erinome(설정 TTS_ENGINE/GEMINI_TTS_MODEL/GEMINI_TTS_VOICE), 대본 전체 1번 호출, 숫자 한글 읽기(spoken_text), 받은 WAV 그대로 저장(C2PA 정보가 잡음 되던 문제), 쉼 기준 장면 분할(find_cuts), 문장부호 기준 자막, 분당 한도면 30·45초 쉬고 재시도, 실패·하루 한도·이상한 길이면 edge-tts(선희)로 대체. 타임라인 engine 기록
- 화면(src/shorts_style.py, src/video_renderer.py): 청록 질문 상자+주제, 사이트 이름 작게, 진행 막대, 카드(톡), 연속 check는 체크 목록 최대 4줄(띵), 숫자 노란 강조·긴 자막 가로 축소, 배경 18%만 어둡게. 배경음악 assets/music/daily-beetle.mp3(90초, 목소리 나오면 줄어듦, 고음 깎음), 효과음은 ffmpeg로 합성. 목소리 apad에 whole_dur(무한 대기로 렌더가 안 끝나던 문제)
- 배경(src/stock_media.py): 담배·술·외국 지폐·무기 등 BANNED_TAGS 결과 제외
- 유튜브 설명(src/youtube_kit.py): 음악 출처 MUSIC_CREDIT(CC BY 4.0 조건)
- 실제 검증: 테스트 403개 통과(tests/test_shorts_v2.py 23개 추가). test_orchestrator.py::test_next_reuses_saved_screenshot_and_file_ids는 이번 작업 전부터 실패(텔레그램 file_id 재사용) — 미해결. 작업 22 글로 실제 흐름 시험 2회(AI 대본 고쳐 쓰기 → Gemini 음성 30초 → Pixabay → 렌더 87~97초), 예전 형식 장부 모두 읽힘. 결과 data/jobs/22/short-v3/short.mp4 텔레그램 전송
- 남은 일: (1) 실행기(jalcheck-worker) 재시작 — 자동 권한 검사에서 막혀 사용자 확인 필요, 재시작 전까지는 예전 방식으로 제작됨 (2) Codex 다시 로그인 후 11f871f 검토 (3) 승인 대기 쇼츠(작업 12·19~22)는 예전 방식 그대로 — 새로 만들려면 별도 결정 (4) 위 기존 실패 테스트

## 2026-11-10 야간 코드 점검 + 작은 수정(실행기 재시작 안 함)
- 지시자: 사용자("자는 동안 코드 리뷰·개선점 파악, 지금은 Claude만, 07시 이후 토큰 사용 금지") / 작업자: Claude / 검토자: 없음 — Codex 검토 대기(사용자 지시로 Claude 단독)
- 보고서: docs/review-2026-11-10-night.md
- 변경: src/gemini_tts.py(음성 요청 제한 시간 180초, 연도 범위·날짜·'1,2월'·'10만~20만' 숫자 읽기, 전화번호 형식 좁힘), src/pipeline.py(Gemini 음성의 어떤 실패든 edge-tts로 대체), tests(숫자 읽기 6개·대체 1개 추가, 다음 글 버튼 테스트를 7e59ee7의 답장 방식에 맞춤)
- 검증: pytest 411 통과(전에 실패하던 1개 포함), genai 클라이언트 제한 시간 설정 생성 확인. 실제 Gemini 음성 호출은 사용량 때문에 안 함
- 발견: 7e59ee7('HANDOFF' 커밋)에 다음 글 버튼 답장 방식 코드가 검토·기록 없이 들어감
- 남은 일: (1) Codex 로그인 후 11f871f + 7e59ee7 + 이번 수정 함께 검토 → 실행기 재시작 (2) 보고서 '결정이 필요한 것' 2~5번 사용자 결정

## 2026-11-11 검토 반영 후 실행기 재시작
- 지시자: 사용자("1. 재시작해줘") / 작업자: Claude / 검토자: Codex BLOCKED(인증 만료) → Gemini gemini-4-argon-medium CHANGES_REQUESTED(REVIEW.md)
- 변경: src/youtube_kit.py(쇼츠 위쪽 질문·장면 카드 숫자도 글 본문과 대조), src/gemini_tts.py(날짜 범위 '~' 읽기), 테스트 3개 추가
- 검증: pytest 414 통과
- 남은 일: Codex 다시 로그인(codex logout → login) 후 재검토, Gemini 낮음 항목 2·4·5 결정
- 실행기 재시작 2026-11-11 03:23 UTC(진행 작업 0건, 승인 대기 5편), active·'실행기 시작' 로그·사이트 200 확인 → 쇼츠 개편·다음 글 답장 방식·야간 수정·검토 반영 적용

## 2026-11-11 국가장학금 글(작업 12) 실수 발행 되돌림
- 지시자: 사용자("1": 비공개로 되돌리기) / 작업자: Claude / 검토자: 없음(글 1개 상태 변경, 코드 변경 없음)
- 경위: 11-06 보류 결정 때 장부를 바꾸지 않아 작업 12가 승인 대기로 남았고 [발행] 버튼이 유효했음. 2026-11-11 03:26:42 UTC 실행기를 통해 발행됨(텔레그램 버튼을 누른 것으로 추정, 누른 기록은 없음)
- 변경: 글 105 publish → draft(date null로 날짜 초기화, 나중에 발행하면 그때 날짜), 장부 작업 12 published → canceled(규칙상 published는 바꿀 수 없어 SQL로 직접 수정). 취소 상태라 [발행] 버튼 무효, `jobctl redo 12` 가능. 백업 ~/backups/2026-11-11-hold-post105/
- 검증: 글 주소·?p=105 404, 홈·사이트 지도에 없음, 공개 API 401, 홈 200, 실행기 active, 승인 대기 목록에서 빠짐
- 남은 일: 11-16 10:13 KST 공고 확인 예약 그대로. 쇼츠는 유튜브에 올리지 않기

## 2026-11-11 Codex 재로그인·재검토
- 지시자: 사용자("1") / 작업자: Claude / 검토자: Codex gpt-6.1-sol high, CHANGES_REQUESTED(REVIEW.md), 코드 변경 없음
- 기기 코드 방식으로 재로그인 성공. codex-review.py는 bwrap 오류로 BLOCKED라 read-only + stdin 전달로 검토
- 지적 4건(모두 중간): 수정 전 화면 캡처 재전송, '2026.1.5~2.10' 끝 날짜를 소수로 읽음, '3.3 %' 퍼센트 누락, 장면 안의 긴 쉼을 경계로 고를 수 있음
- 남은 일: 4건 수정 여부 사용자 결정. 실행기는 이미 이 코드로 운영 중

## 2026-11-11 쇼츠 숫자 읽기 수정 + 실행기 재시작
- 지시자: 사용자("1") / 작업자: Claude / 검토자: Codex gpt-6.1-sol high PASS(REVIEW.md, 코드 추적 기준)
- 변경: src/gemini_tts.py(연도를 줄인 날짜 범위 2026.1.5~2.10, 띄어 쓴 % 읽기), tests/test_shorts_v2.py 6개 추가
- 검증: pytest 420 통과. 실제 Gemini 음성 호출은 안 함
- 실행기 재시작 2026-11-11 03:38 UTC(진행 작업 0건)
- 남은 일(보류): 장면 안의 긴 쉼을 경계로 고를 수 있음(find_cuts), 수정 전 화면 캡처 재전송

## 2026-11-11 쇼츠 장면 경계 수정 + 승인 대기 3편 새 방식으로 교체
- 지시자: 사용자("1": 경계 고친 뒤 3편 다시 만들기) / 작업자: Claude / 검토자: Codex CHANGES_REQUESTED 2회, 남은 2건은 Claude가 수정 안 하기로 판단(REVIEW.md)
- 커밋: f16903d(find_cuts: 문장부호·장면 끝과 실제 쉼을 순서대로 맞춤, 장면별 말 속도 검사), 8a046c6(배경: ai generated·anime·green screen 등 제외, 쇼츠 배경은 검색어 2단어 이상 일치, 금액 자막 안 갈림, 대본 조건·단서 유지 규칙), 3742100(경계 위치 오차 가중, 동물·크리스마스 제외는 쇼츠 배경만)
- 교체: 작업 20·21·22 checkpoint content.shorts·tts·media.scenes·video와 video_path를 data/jobs/N/short-30s로, 승인 요청 재전송(nonce 89c754e6·92f10634·797d68da). 글·임시 글은 안 바꿈. 예전 영상 data/jobs/N/short 유지. 장부 백업 data/backups/jobs-before-shorts-remake-20261111-063754.sqlite3. target_seconds 칸은 45 그대로
- 발견·수동 수정: AI 대본이 숫자는 맞아도 조건을 왜곡함(20: '6월 전 폐업'·'고지서 50만 미만'·'신고하세요', 21: '둘째아·쌍둥이면 받아요'(지자체 승인 필요)·'최대 152만'(첫째아 기준 152만 5,000원), 22: '모르면 한 푼도'). 글과 대조해 Claude가 고침. 숫자 대조 검사로는 못 잡는 종류라 승인 시 사람 확인 필요
- 배경: 일부 장면은 맞는 영상이 없어 단색 배경(21번 3장면, 22번 3장면, 20번 1장면)
- 실제 검증: pytest 426 통과, 3편 음성 받아쓰기로 문장·장면 시작 시각 일치 확인, 화면 캡처 확인. 마지막 자막·배경 재렌더 뒤 소리는 그대로
- 남은 일: 21번 승인 메시지에 '5,000' 숫자 경고가 뜨는데 글의 1,525,000원과 표기만 다른 같은 값(검사 오탐), 수정 전 화면 캡처 재전송(보류)
- 2026-11-12 예약 알림 확인: Googlebot의 wp-sitemap 요청은 여전히 없음(sitemap 200 정상). 대신 11-09 이후 구글이 공개 글 18개 중 대부분과 소개·문의·분류 페이지를 직접 수집(모두 200). 참고: 11-11 Googlebot을 사칭한 해킹 탐색(.env·wp-config.php.bak·.git 등)이 있었으나 모두 404/403, 노출 없음 확인
- 2026-11-12 사용자가 서치 콘솔에 사이트맵 다시 제출(이전 제출은 등록 안 돼 있었음) → 13:55~14:00 UTC 구글(66.249.79.x)이 wp-sitemap.xml과 하위 사이트맵 3개를 모두 200으로 가져감. 콘솔 표시는 늦게 '성공'으로 바뀔 예정
- 2026-11-12 사용자가 작업 21·22 쇼츠 확인: 큰 문제 없음, 승인·업로드는 나중에 직접 할 예정(승인 대기 유지)
- 2026-11-12 사용자 결정("1"): 배경 금지 단어 'bill/bills' 그대로 유지(단색 배경이 자주 보이면 재검토), 음악 출처 줄 항상 붙이는 동작 그대로 유지(예전 방식 영상은 더 안 생김, 19번만 해당). 코드 변경 없음

## 2026-11-12 미업로드 쇼츠 16~19 새 방식으로 교체
- 지시자: 사용자("1": 16~19 모두 새 방식으로 다시 만들기) / 작업자: Claude / 검토자: Gemini gemini-4-argon-medium(기존 쇼츠 13~19 대본·글 대조), 새 대본은 Claude가 글과 직접 대조. 코드 변경 없음
- 배경: 13~15는 유튜브에 이미 올림 → 그대로 둠(15번 '감면 조건은 글에서' 정도만 낮음). 16~19는 미업로드. 대조 결과 16번 '880만 원을 정부와 회사가 나누어'(880만은 정부 급여 상한, 고임금이면 회사 차액 추가) 중간, 16번 미적용자·180일 미충족 혼합 낮음, 19번 '만 9세 미만이면 모두' 낮음
- 방법: 기존 글 내용으로 쇼츠 대본만 새로 생성(SHORTS_SYSTEM_INSTRUCTION, 30초, 조건 유지 지시) → check_content 통과 → Claude 수정(16: 660만·880만을 '급여 상한'으로, 150만 '요건이 맞으면' / 17: '서울 1인 임차가구' / 19: 5장면 '난민인정 심사 중 제외', 제목 '최대 13만 원') → Gemini 음성·배경·렌더(nice 15)를 data/jobs/N/short-30s에. 예전 영상 data/jobs/N/short 유지
- 장부: checkpoint content.shorts·tts·media.scenes·video와 video_path(절대 경로) 교체, 백업 data/backups/jobs-before-shorts-remake-20261112-152311.sqlite3. 텔레그램 업로드 안내(영상·제목·설명·다음 글 버튼) 16~19 재전송
- 검증: 4편 음성 받아쓰기가 대본과 일치(숫자 모두 맞음), 장면 사진 확인(길이 25~31초, 단색 배경 17번 2장면·18번 1장면, 19번 한 장면 배경에 페이스북 로고 보임), 실행기 active
- 작업용 스크립트는 저장소에 넣지 않음(1회성): 대본 생성·수정·렌더·받아쓰기 순서는 위와 같음

## 2026-11-13 발행 전 자동 검토 단계 + 숫자 대조 오탐 수정
- 지시자: 사용자("응": 승인 요청의 '확인할 것'을 AI가 먼저 처리) / 작업자: Claude / 검토자: Codex gpt-6.1-sol high(stdin 전달, bwrap 오류 우회) 1차 CHANGES_REQUESTED 6건 → 수정 → 2차 5건 해결·2건 추가 지적 → 수정
- 배경: 작업 22 승인 알림의 '못 찾은 숫자' 8개가 모두 표기 차이 오탐(자료 '1억3천5백만원'·'349,700원' ↔ 글 '1억 3,500만 원'·'34만 9,700원'). 반대로 숫자 검사로 못 잡는 진짜 문제(소득 기준과 지역별 재산 공제액을 한 표에 섞음)는 알림에 없었음
- 변경(커밋 9a95b1f): extract_figures 한글 복합 금액 파싱, missing_figures(자료 표의 맨숫자 인정, 단위 붙은 숫자·날짜 조각·100 미만 제외), ContentGenerator.review(글 생성 직후 AI가 자료와 대조해 수정 → 검사 통과·미확인 숫자 증가 없을 때만 채택, 대본 길이만 틀리면 대본만 다시 맞춤, 실패·오류 시 처음 글 유지), takeaway 3곳 초과 정리, QUIET_WARNINGS(문체 참고·'자료로 확인 안 된 내용'·링크 정보)는 알림에서 숨김, 텔레그램 '자동 검토에서 고친 것'/'확인할 것' 분리(없으면 '확인할 것 없음')
- 검증: pytest 434 통과. 지난 작업 전체 '못 찾은 숫자' 203 → 31. 실제 Gemini로 작업 22 글 검토: 섞인 표를 가구별 선정기준액 표로 정리하고 재산 공제액은 문단으로 옮김, 남은 경고 0(장부·워드프레스는 안 바꿈, 결과는 임시 파일)
- 실행기 재시작 2026-11-13 05:02 UTC(진행 작업 0건). 글 한 편마다 Gemini 호출 1회(대본 길이 틀리면 최대 3회) 늘어남
- 2026-11-13 사용자 결정("1"): 작업 22 글 157을 검토본으로 교체(쇼츠는 사용자가 확인한 기존 것 유지, 사진·분류 그대로). 장부 content·draft 갱신(fixes 2줄, 확인할 것 0), 승인 요청 재전송(nonce 476591a9, 새 화면 캡처). 백업 data/backups/2026-11-13-job22-review/(장부, 글 157 이전 본문). 참고: 작업 22의 target_seconds 칸은 45지만 실제 쇼츠는 30초 기준
- 2026-11-13 사용자 결정("1"): 승인 대기 작업 23~32에도 같은 검토 적용(1회성 스크립트, 저장소에 안 넣음). 쇼츠·사진·분류 유지, 6편 글 수정(24·25·28·29·30·31), 4편 변경 없음(23·26·27·32), 10편 모두 새 형식으로 승인 요청 재전송. 백업 data/backups/2026-11-13-jobs23-32-review/(장부, 글별 이전 본문)
  - 남은 경고 직접 확인: 25 '170만·600만·102만'은 자료의 17%·연 1,000만 원 한도로 계산한 예시(맞음), 26 '15%'는 자료의 광산구 선할인 10%+후캐시백 5% 합계(맞음), 27 경정청구 기한 5년(국세기본법) 판단 맞음, 29 '199만 원'은 4인 1,994,600원을 줄인 쇼츠 표기
  - 주의: 29·30 알림의 '고친 것'에 쇼츠 대본·배경 수정이 적혔지만 쇼츠는 그대로 둠(1회성 스크립트 한계, 실행기 코드는 쇼츠 수정도 반영함)

## 2026-11-13 다음 글 안내에 남은 승인 대기 번호 표시
- 지시자: 사용자("1": 어디까지 발행했는지 매번 보이게) / 작업자: Claude / 검토자: 없음(표시 문구만 바뀌는 작은 변경)
- 변경: src/orchestrator.py waiting_line() 추가 → '다음 글로 이동' 메시지와 보류·취소·쇼츠 없는 발행 뒤 안내에 "남은 승인 대기 N개: 23, 24, …"(최대 15개) 한 줄. tests/test_orchestrator.py 확인 1줄
- 검증: pytest 434 통과, 실행기 재시작 05:46 UTC(진행 작업 0건, 승인 대기 23~32) active
- 2026-11-13 사용자 요청으로 복원 암호를 텔레그램에 1회 재전송(대화·로그 미출력, protect_content). 메시지 번호 data/.backup_pass_message.json — 사용자가 저장 확인하면 deleteMessage로 삭제(48시간 안)
- 2026-11-13 사용자가 복원 암호 저장 확인 → 텔레그램 암호 메시지 삭제(deleteMessage True), 메시지 번호 기록 파일 삭제

## 2026-11-14 구글 "잘체크" 검색 시 문의 페이지가 먼저 뜨는 문제 (지시: 사용자 / 작업: Claude / 검토: 없음, 화면에 안 보이는 한 줄 추가)
- 상황: 구글에서 "잘체크" 검색 시 홈 대신 /contact/가 맨 위. 서치 콘솔 URL 검사상 홈은 이미 색인됨. 구글이 "잘체크"를 아직 일반 낱말로 보고, 본문에 이 말이 문장으로 반복되는 문의 페이지를 더 맞는 결과로 고른 것으로 판단(사이트 개설 1주 미만)
- 변경: deploy/wp/mu-plugins/jalcheck-seo.php 홈 WebSite 구조화 데이터에 alternateName "jalcheck" 추가 → 서버 mu-plugins 반영(백업 data/backups/alt-name-20261114-113509)
- 보류한 안: 홈 큰 문장을 "잘체크는 …"으로 바꾸기. 효과 불확실·로고 바로 아래 이름 중복으로 어색함 → 2~3주 지켜본 뒤 그래도 문의 페이지가 뜨면 재검토(사용자 선택 1번)
- 검증: 홈·문의·글 200, 홈 JSON-LD에 "name":"잘체크","alternateName":"jalcheck" 출력 확인
- 남은 일: 사용자 서치 콘솔에서 홈 [색인 생성 요청](선택), 11월 말~12월 초 "잘체크" 검색 결과 재확인

## 2026-11-15 하루 1편 발행 안전장치 (지시: 사용자 / 작업: Claude / 검토: Gemini argon-high, Codex는 실행 환경 오류로 대체)
- 요청: 수동 발행(하루 1~2편)을 잊는 날에도 하루 1편은 꼭 올라가게
- 사용자 결정: ① 승인 대기 글이 없으면 알림만(새 글을 만들어 바로 올리지 않음) ② 저녁 7시 알림, 밤 10시 자동 발행(한국 시간) ③ 가장 오래된 승인 대기 글부터
- SEO 판단: 발행 시각은 구글·네이버 순위에 영향 없음, 꾸준함이 중요 → 시간은 사용자가 직접 고를 여유 기준으로 정함
- 방식: 처음엔 systemd 타이머를 검토했으나, 발행 코드(handle_decision)·텔레그램이 실행기 안에 있고 주간 주제 후보도 같은 방식이라 실행기 스레드(5분마다, 시작 즉시 1회)로 구현
- 변경: src/daily_guard.py(새 파일), src/orchestrator.py(daily-publish 스레드), src/wp_client.py published_since(워드프레스에서 오늘 발행 글 확인, 관리자 화면에서 직접 올린 글 포함), tests/test_daily_guard.py, tests/test_wp_client.py
- 동작: 19시 오늘 발행 0편이면 예정 글의 승인 요청 메시지에 답장으로 알림(대기 2개 이하면 채우라는 문구) → 22시에도 0편이면 가장 오래된 대기 글을 버튼과 같은 경로로 발행. 하루 상태는 data/daily_guard.json
- 검토 지적 4건 수정: 버튼과 경쟁 시 2편 발행 가능성(워드프레스가 분명히 거절할 때만 다음 글로, 직전 장부 재조회), 결과 불확실 시 그날 포기(기록 안 하고 5분 뒤 재판단), 알림 실패 시 상태 미저장, 재시작 직후 5분 대기 → 2차 검토 APPROVED
- 검증: pytest 448 통과, 실제 워드프레스 조회로 오늘 발행 글 171 확인, 실행기 재시작 2026-11-15 09:59 UTC active(진행 작업 0건)
- 남은 일: 실제 19시·22시 동작은 발행 안 한 날 텔레그램으로 확인. 시간 바꾸려면 daily_guard.py REMIND_AT·AUTO_AT 수정 후 실행기 재시작

## 2026-11-15 (같은 날 변경) 규칙 바꿈: 매일 오전 8시 무조건 1편 자동 발행 (지시: 사용자 / 작업: Claude / 검토: Gemini argon-high 2회, 1차 지적 3건 수정 후 APPROVED)
- 사용자 결정: 저녁 알림·밤 자동 발행 대신 매일 오전 8시(한국 시간)에 그날 발행 여부와 상관없이 가장 오래된 승인 대기 글 1편 발행. 텔레그램은 직접 [발행]을 눌렀을 때와 똑같이(별도 '자동 발행' 메시지 없음)
- 변경: src/daily_guard.py 다시 작성(1분마다 확인, 8시에 꺼져 있었으면 그날 켜지는 즉시), published_since(워드프레스 오늘 발행 조회)는 필요 없어져 제거. 대기 글이 없을 때만 "주제를 등록해 주세요" 알림(앞선 결정 ① 유지)
- 검토 지적 수정: 응답 끊김이 계속될 때 무한 재시도·알림(하루 3번 시도 상한), 거절된 글 실패 알림 반복(그날 제외 목록), 8시 정각 직접 발행과 겹치면 1분 뒤 2편째 발행(직접 올린 글을 오늘 1편으로 인정)
- 검증: pytest 446 통과. data/daily_guard.json에 오늘(11-15)은 끝남으로 기록 후 실행기 재시작 10:24 UTC active → 오늘 밤 즉시 발행 안 함, 첫 자동 발행은 11-16 08:00 KST(작업 25 예정)
- 시간 바꾸려면 daily_guard.py PUBLISH_AT 수정 후 실행기 재시작

## 2026-11-15 주제 자동 등록 (지시: 사용자 / 작업: Claude / 검토: Gemini argon-high 2회, 1차 지적 5건 수정 후 APPROVED)
- 요청: 아침 자동 발행으로 대기 글이 하루 2~3편씩 줄어드니 주제 등록도 자동으로
- 사용자 결정("ㅇ"): 글쓰기 중+승인 대기 글이 5편보다 적으면 하루 한 번(오전 9시 이후) 모자란 만큼, 하루 최대 3편 등록. 뉴스에서 새로 찾은 제도·기존 글 고치기는 자동 등록 안 함(기본 제도 목록만, 틀린 정보 위험). 월요일 후보 메시지는 그대로
- 변경: src/topic_service.py(auto_register_if_due, 등록 공용 _register_candidate — [n번 등록] 버튼과 같은 코드·같은 주제 중복 방지), src/topic_finder.py(find_candidates discover=False), src/topic_store.py(회차 종류 'auto' 추가: SQLite 제한을 못 고쳐 batches 표를 새로 만들어 옮김, 번호·주 1회 색인 유지), src/orchestrator.py(10분마다 도는 주제 스레드에 연결), tests/test_topic_auto.py
- 판단: 자동 등록은 '보냈는데 안 고른 후보 3주 쉬기'를 적용하지 않음 — 사람이 안 눌렀다고 빼면 상위 주제가 빠지고 목록(기본 50개)이 금방 바닥남. 자동으로 넣은 주제를 월요일 메시지에서 또 누르면 기존 중복 방지가 막음. 자동 회차의 남은 후보는 주간 쉬기 목록에 넣지 않음
- 실패 처리: 찾기 실패는 1시간 뒤 1번 재시도(2번째 실패 때 알림), 등록 시작 후 오류는 그날 재실행 없이 바로 알림
- 검증: pytest 459 통과, 운영 주제 장부 복사본으로 표 옮기기 확인(회차 4·후보 30 그대로, 색인 유지), 실제 검색 미리 보기(장부 기록 없음)로 1순위 "국가장학금 지원 대상과 신청 방법" 등 5개. 백업 data/backups/topics-before-auto-20261115.sqlite3
- 위험: 자동 등록 글도 승인 대기 → 아침 8시 자동 발행이라 사람이 안 본 주제가 올라갈 수 있음. 대기열 맨 뒤라 보통 1~2일 여유, 그사이 승인 요청에서 [취소]
- 숫자 바꾸려면 topic_service.py AUTO_TARGET·AUTO_MAX_PER_DAY·AUTO_HOUR 수정 후 실행기 재시작

## 2026-11-15 발행된 글 173(작업 25, 월세 세액공제) 사실 오류 수정 (지시: 사용자 / 작업: Claude)
- 계기: 텔레그램 "확인할 것"(숫자 170만·600만·102만, 홈택스 자료를 AI가 옮겨 적음) → 검토해 보니 세 숫자는 계산 예시로 맞았음(거짓 경보). 대신 자동 검사가 못 잡은 문제가 있었음
- 고친 것(REST API로 본문만 교체, 상태 publish 유지): 자동 검토가 자료에 없다고 지운 '과세기간 종료일(12월 31일) 현재 무주택' 복원(조특법 §95의2 요건), 엉뚱한 기준 줄(2024.12.31 주택 등기현황) → 조특법 §95의2·2026 귀속 현행, 주택 요건 표 '둘 중 하나만 충족', 결정세액보다 많이 공제 안 됨 주의 추가, '감면'→'세액공제', 2026.8 세제개편안(총급여 8천 이하 청년 17%, 국회 심의 중) 안내 추가(뉴시스 원문 확인, 한도 1,200만 원설은 보도가 엇갈려 제외), 출처 이름 정리·법령 링크 추가, 자료 확인일 11-15
- 백업: data/backups/post173_202611151049.sql
- 검증: https://jalcheck.com/monthly-rent-tax-credit-guide/ 200, 수정 문구 4곳 실제 페이지에서 확인
- 교훈(다음 작업): ① 숫자 검사가 글자 그대로 비교라 계산 결과를 오류로 봄 ② 자동 검토가 '자료에 없음 = 틀림'으로 맞는 내용을 지움 ③ 출처가 홈택스 첫 화면 등 약함. 아침 8시 자동 발행·주제 자동 등록이 있어 사람 확인 없이 올라갈 수 있으므로 자동 사실 확인 강화가 필요(계획 보고 후 진행 예정)
- 2026-11-16 예약 확인(국가장학금 작업 12): 네이버 뉴스(최신순)·검색상 2027학년도 1학기 1차 신청 공식 공고·가~마 구간 단가 아직 없음(블로그들만 '11월 말~12월 말 예정' 인용). 작업 12 그대로 보류(canceled), 다른 국가장학금 작업 없음. 예약 trig_01StBKmtsbAdvLa7n2aqgaiE를 11-20 10:13 KST로 다시 걸음. 주의: 주제 자동 등록 후보 1순위가 '국가장학금 지원 대상과 신청 방법'이라 공고 전 자동 등록되면 아침 8시 자동 발행될 수 있음 → 승인 요청 오면 [취소]

## 2026-11-16 자동 확인 개선 ① 계산 검산 (지시: 사용자 / 작업: Claude / 검토: Gemini argon-high 2회, 1차 지적 5건 수정 후 APPROVED. Codex는 bwrap 오류로 파일을 못 읽어 대체)
- 요청: 텔레그램 "확인할 것"을 사람이 하지 않게, 알아서 확인 끝내고 가져오기. 3단계 계획(① 계산 검산·알림 정리 ② 원본 자료 강화(법령 조문) ③ 별도 사실 확인 단계·최근 개편안 검색) 중 ①
- 원인: missing_figures가 글자 그대로 비교라 '600만 원 × 17% = 102만 원' 같은 계산 예시를 "자료에서 못 찾은 숫자"로 보고
- 변경(src/ai_generator.py): derived_figures·calc_check_note 추가. 같은 문단에서 '금액 × 자료의 비율(0<r<100)'로 계산되면 경고에서 빼고 "자료의 숫자로 검산 확인: …"을 fixes 맨 앞에 보고. 텔레그램 칸 제목 "자동 검토에서 고치고 확인한 것"
- 판단(리뷰 반영): 곱하는 쪽 금액이 자료에 없으면 '만약·했다면·라면' 같은 예시 문장에 있을 때만 가정값으로 인정(지어낸 한도+계산값 짝 면제 방지). 면제는 같은 문단 안에서만(제목·메타설명·쇼츠 제목은 본문 검산을 따름). 오차는 표시 자릿수 반올림만(최대 0.5%). 자동 검토 채택 판정(_accept_review)은 with_calc=False로 예전과 같은 엄격 기준
- 검증: pytest 466 통과(test_orchestrator_routes_topic_buttons 1건은 변경 전부터 실패, 별건), 과거 작업 전체 비교 시 달라진 것은 작업 25뿐(못 찾은 숫자 3개 → 0개, 검산 줄 출력)
- 남은 일: ② 법령 조문 자료 추가·'AI가 옮겨 적은 자료' 경고 줄이기, ③ 사실 확인 단계. 실패 테스트 원인 확인(별건)

## 2026-11-16 백업 덮어쓰기 보호·날짜 단위 보관 (지시: 지휘자, 작업: Claude 오라클1, 검토: Gemini argon-high)
- 원인: ① 서버 안 ~/backups/auto가 비거나 망가진 채 실행되면 GitHub 강제 덮어쓰기로 지난 백업까지 잃음 ② 보관을 파일 개수로 세서 하루 여러 번 실행(11-09 3회)하면 지난 날짜가 밀려 지워짐
- 변경(scripts/backup_site.py, tests/test_backup_site.py): remote_names·guard_remote 추가(GitHub에만 있고 남겨야 할 백업이 있으면 올리지 않고 텔레그램 알림, 목록 오류도 실패 처리), old_files를 날짜 단위로(그날 마지막 백업, 월별본도 날짜별 마지막 중 첫째). 오라클1 ~/tools/oracle1-backup과 같은 코드
- 리뷰 반영: 월별본을 그날 첫 백업으로 고르면 11-09 같은 날 다회 실행 뒤 8~14일 차에 보호가 잘못 막는 문제, clone에 -b main 지정
- 검증: test_backup_site 18개 통과, 실제 GitHub 목록으로 정리 후 보호 검사 통과. 실제 업로드는 다음 04:00 KST 정기 실행으로 확인
- 남은 일: 다음 정기 실행 로그(journalctl -u jalcheck-backup) 확인

## 2026-11-16 자동 확인 개선 ② 현행 법령 조문을 근거 자료로 (지시: 사용자 / 작업: Claude / 검토: Gemini argon-medium 2회, 1차 지적 반영 후 APPROVED. Codex는 bwrap 오류로 대체)
- 이유: 승인 요청의 "확인할 것"을 줄이려면 안내 페이지보다 원문인 법령과 대조해야 함. 글 173 오류(배우자 추가공제 누락 등)도 법령에 있던 내용.
- 흐름(src/law_source.py): 국가법령정보 지능형 검색(aiSearch, 주제 문장 + 제도 이름 두 번) → Gemini가 후보 key 안에서만 최대 4개 선택(지어내기 불가) → lawService로 **현행 원문 재조회** → Source(via="law"). 인증값 `.env`의 `LAW_OC`(비우면 법령 없이 작성).
- 재조회 이유: 지능형 검색은 옛 버전 조문을 돌려줄 때가 있음(실측: 2026-09 전부개정되어 '인구전략기본법 시행령'이 된 법의 옛 조문). 폐지 법령(“일치하는 법령이 없습니다”), 번호가 사라진 조문, 시행 전 조문(조문시행일자가 미래), 같은 번호에 제목이 바뀐 조문은 빼고 source_errors에 기록.
- 법령 API 실패·이상한 응답은 어떤 경우에도 글 작성을 멈추지 않음. 단, 웹 공식 안내 자료가 하나도 없으면 법령만으로는 쓰지 않음(신청 방법·누리집이 없으므로).
- 프롬프트(LAW_NOTE): 법령 원문 우선, '100분의 17'→'17%'로 풀어 쓰기, 안내 자료와 다르면 법령을 따르고 unverified에 기록. 숫자 대조는 '100분의 16.5' 같은 분수를 %와 같게 봄.
- 사이트 첫 화면 주소(hometax.go.kr/ 등)는 근거 자료에서 제외(is_home). 바로가기 링크로는 다른 자료 본문에 그 도메인이 적혀 있을 때만 남음 — 수용한 한계.
- 'AI가 옮겨 적은 자료' 경고는 via=="gemini"(원격 판독) 자료에만.
- 실제 검증: 테스트 489개 중 488 통과(실패 1건은 원래 있던 test_topic_service 별건). 실시간: 월세 → 조특법 95조의2·시행령 95조, 부가세 기한 → 부가세법 48·49조·국세기본법 5조, 전기요금 → 없음(정상), 첫만남 → 옛 법 조문 자동 제외. 월세 주제 실제 작성 시험(발행 안 함)에서 배우자 추가공제가 법령 근거로 자동 반영, 경고는 "못 찾은 숫자 150만·170만 원" 1건만(한도×공제율로 계산한 최대 금액).
- 실행기 재시작 완료(새 코드 적용).
- 남은 일: 한도×비율로 나온 '최대 ○○만 원' 인정(검산 확장), ③ 사실 확인 단계·최근 개편안 검색(승인 후), 경정청구 주제는 국세기본법 45조의2가 후보에 안 잡힘.

## 2026-11-16 검산 확장: 한도가 다른 문단에 있는 '최대 금액' 인정 (지시: 사용자 / 작업: Claude / 검토: Gemini argon-medium 2회, 1차 지적 4건 반영 후 APPROVED)
- 이유: 월세 글 시험에서 남은 마지막 경고가 "최대 150만·170만 원"(= 한도 1,000만 원 × 15%·17%). 한도와 비율이 같은 소제목의 서로 다른 문단에 있어 문단 안 검산(①)이 못 잡음.
- 규칙(src/ai_generator.py _derive_in_context): 곱하는 금액·비율이 **둘 다 자료에 있는 숫자**, **같은 소제목 안**, 둘 중 하나는 **그 문단(표는 같은 줄)에 직접** 있을 때만 인정. 소제목 밖(도입·FAQ·마무리)은 넓은 범위 검산 안 함, 제목·요약은 기존대로 본문에서 검산된 금액만.
- 유지한 원칙: '가입 한도는 170만 원입니다'처럼 계산과 무관한 단정문은 인정 안 함.
- 알고 받아들인 한계: 금액만 문단에 있으면 억지로 맞는 계산도 통과할 수 있음(예: '3,200만 원 이하면 최대 480만 원' = 3,200만 × 15%). 한도만 적힌 요약 문장을 살리기 위한 선택이며, 통과한 계산은 승인 요청에 계산식으로 그대로 보임(calc_check_note).
- 검토 반영: 글자 내용을 키로 쓰면 같은 글자의 표 칸이 다른 소제목 근거로 섞이는 문제 → 글 구조를 따라 도는 _text_units로 교체(_blog_texts 삭제).
- 검증: 테스트 493개 중 492 통과(기존 별건 1). 월세 시험 글: 못 찾은 숫자 0건, "150만 원 = 1,000만 원 × 15%", "170만 원 = 1,000만 원 × 17%" 검산 확인.

## 2026-11-16 자동 확인 개선 ③-B 사실 확인 단계 + 아침 자동 발행 보류 (지시: 사용자 / 작업: Claude / 검토: Gemini argon-high 2회, 1차 지적 8건 중 7건 반영 후 APPROVED)
- 이유: 아침 8시 자동 발행·주제 자동 등록 때문에 사람 확인 없이 글이 올라갈 수 있다. 숫자 대조만으로는 조건·대상·기한 문장의 오류를 못 잡는다.
- 흐름(make_content): generate → review → **fact_check**(src/ai_generator.py).
  1. AI가 사실 문장(최대 20개)을 골라 자료와 대조하고 supported/contradicted/unsupported로 판정한다.
  2. supported는 프로그램이 근거 인용을 자료 본문과 다시 확인한다. 인용의 낱말(숫자는 한 글자도)이 모두 같은 자료 한 곳에 없으면 근거 없음으로 본다(`_quoted_in_sources`). 처음에는 연속 글로 대조했는데, AI가 표 칸이나 떨어진 구절을 이어 인용해 실제 글에서 오탐이 3~5개 나와 낱말 단위로 바꿨다.
  3. 문제가 있으면 기존 검토 지시문으로 한 번 고친다(`_accept_review` 통과 시만 채택). 고치면서 바뀐 문장은 다시 대조하고, 고치는 AI의 unresolved도 남은 문제로 본다.
  4. 남은 문제가 있으면 경고 "사실 확인: …"(최대 5줄)를 붙이고 `fact_check.hold=True`. 모델 오류로 확인을 못 해도 hold(글 전체 재작성 대신 보류해 호출 낭비 방지, 사람은 [발행] 가능).
- daily_guard: hold 글은 아침 자동 발행에서 건너뛴다. 보류 글만 남으면 "사실 확인 보류" 알림을 보낸다. 사실 확인 도입 전 글(기록 없음)은 예전대로 발행 대상이다. jobctl redraft는 '사실 확인:' 경고를 보존한다.
- AI 호출: 글당 +1(문제 없음) ~ +3(대조·고치기·재대조). 시간은 30초~1분 증가.
- 실측
  - 월세·부가세 기한·첫만남 글: 오탐 0
  - 부가세 글에 틀린 문장 2개(간이과세자 매달 신고·과태료 50만 원, 개인도 4번 확정신고)를 넣은 시험: 2개 모두 잡아 고침, 재대조 통과
- 테스트: 사실 확인 10개, daily_guard 3개 추가. 전체 505 통과, 1 실패(별건 test_topic_service: 고정 날짜 2026-11-08이 7일 만료를 넘김, Gemini 확인)
- 남은 일: (사용자 결정 2026-11-16) 며칠간 실제 글의 "사실 확인:" 표시를 지켜본 뒤 ③-A 최근 변경(개편안) 찾기 착수. 점검 기준: 맞는 문장을 잘못 잡은 경우(오탐)가 있는지, 보류로 아침 발행이 빠진 날이 있는지. 별건 테스트 날짜 수정도 남음

## 2026-11-17 첫 사진 본문 누락 수정 + '기준: … 기준' 중복 제거 (지시: 사용자 / 작업: Claude / 검토: 없음 — 화면 표시용 작은 수정, 로그인·DB 무관. 원인 파악은 Gemini argon-high UI 검토)
- 원인(사진): 11-05 이전에는 글 맨 위에 대표 사진이 있어 "대표 사진은 본문에 다시 넣지 않음"으로 중복을 막았다. 이후 사용자 지시(A1)로 글 화면 맨 위 대표 사진을 뺐는데 앞 규칙이 그대로 남아, 첫 사진이 홈 카드·공유 미리보기에만 나오고 글 안에서는 사라졌다(사진 1장 찾은 글은 본문 사진 0장. 예: 새출발기금 글).
- 수정: src/pipeline.py make_draft — 첫 사진은 대표 사진으로 등록하면서 본문 제 소제목 아래에도 넣는다. 글 맨 위 대표 사진 칸은 계속 없음(사용자 결정 유지).
- 원인(문구): reference_note는 "○○ 기준"으로 끝나게 생성(content_schema.py:100)되는데 본문 표기가 "기준: "을 앞에 붙여 "기준: … 기준"이 됐다.
- 수정: src/blog_html.py reference_note_text — 끝의 '기준'을 떼고 "기준: ○○"로 표시. 유튜브 설명의 "정보 기준: … 기준"은 손대지 않음(남은 일).
- 적용 범위: 새로 만드는 글부터(실행기 2026-11-17 재시작으로 적용). 이미 발행된 글의 빠진 사진·중복 문구는 그대로(남은 일, 미리보기 후 일괄 수정 필요).
- 검증: 테스트 2개 추가. 전체 509개 중 508 통과(실패 1건은 기존 별건 test_topic_service).
- UI 검토 남은 일(우선순위, 사용자 확인 2026-11-17): ③ 주제와 안 맞는 대표 사진 → ④ 홈 최신 목록 모바일 목록형 → ⑤ 모바일 여백 축소 → ⑥ 글 첫 화면 도입부 줄이기(사실 확인 안정 후) → ⑦ 기존 글 사진 복원 → ⑧ 카테고리 메뉴 글자색·현재 위치, ⑨ 형광펜 길이 제한. 불필요 판정: 줄 간격 축소, 스크롤 시 메뉴 접기, 요약 박스(이미 목차 있음). Gemini 검토 전문: ~/.claude/ask-ai/logs/20261115-192449-595642-리뷰.md

## 2026-11-18 글 유효기간 점검(1·2단계: 다시 볼 날짜 + 텔레그램 알림) + 새 주소에 연도 빼기 (지시: 사용자 / 작업: Claude / 검토: Codex gpt-6.1-sol high 3회, 1차 5건·2차 3건 반영 후 APPROVE)
- 이유: 구글 SEO 기본 가이드 점검 결과 가장 큰 개선점은 '낡은 정보'. 해마다 바뀌는 금액·마감이 지난 기간이 그대로 남으면 검색 신뢰도가 떨어진다. 같은 주소에서 실제로 내용을 고칠 때만 수정일을 바꾸는 방식이 안전(사용자 질문 "노출 봇·블로그 지수" → 사람 승인·사실 확인을 거친 실제 갱신이면 문제없음).
- 동작(src/freshness.py, 실행기 스레드 freshness_check, 10분마다 확인해 한국 시각 10시 이후 하루 한 번)
  1. 발행 글 중 새 글·수정된 글만 Gemini에게 '시간 지나면 틀려지는 정보'와 다시 볼 날짜를 묻는다(하루 30편, 재시도 포함 누적 days.planned).
  2. 날짜 규칙(decide): 작성일보다 이른 날짜는 버리고, 지난 날짜는 내일로, 너무 먼 날짜는 수정일+400일로 자른다. 못 찾으면 수정일+1년. 사진만 고친 글도 지난 마감이 남아 있으면 바로 알리도록 작성일(date_gmt) 기준으로 거른다.
  3. 날짜가 된 글을 텔레그램 한 메시지로 알림(최대 10편·3800자, 안 알린 글 먼저). 고치지 않으면 7일 뒤 재알림. 전송 실패 시 알림 기록 롤백.
  4. 하루 최대 3번 시도, 같은 수정본에서 3번 실패한 글은 건너뜀(failures 표), 실행기·수동 실행은 파일 잠금(data/locks/freshness.lock)으로 겹치지 않음.
- 장부: data/freshness.sqlite3 (posts, days, failures). 보기: `.venv/bin/python scripts/freshness_check.py show`, 지금 판단만: `plan`, 판단+알림: `run`.
- 새 글 주소(slug)에 연도 넣지 않음: content_schema.slug_without_year + 생성 지시문 수정. 이미 발행된 글 주소(2026 포함 7편)는 바꾸지 않음(주소 변경은 검색 순위 손해). 기존 초안을 다시 저장하면 연도 없는 주소로 바뀜(비공개라 무해).
- 파일: src/freshness.py(신규), scripts/freshness_check.py(신규), tests/test_freshness.py(신규, Codex 작성 34개), src/wp_client.py(list_published_posts), src/ai_generator.py(structured), src/orchestrator.py(스레드), src/content_schema.py, src/pipeline.py
- 검증: 운영 30편 날짜 판단 완료(예: 청년도약계좌 11-28, 문화누리카드·종합소득세 12-01, 자녀장려금 12-02, 종부세 12-16, 다수 2027-01-05). 테스트 542개 중 541 통과(실패 1건은 기존 별건 test_topic_service). 운영 장부의 옛 days 표에 planned 칸 자동 추가 확인.
- 남은 일: ③ "글 OO번 갱신" 요청 시 같은 주소에서 자료 다시 찾기 → 사실 확인 → 승인 후 수정(하루 갱신 상한 별도) ④(선택) 공식 출처 변경 감시.

## 2026-11-18 글 유효기간 점검 3단계: 발행 글 같은 주소에서 갱신 (지시: 사용자 / 작업: Claude, 시험 Codex gpt-6.1-terra / 검토: Codex gpt-6-astra high 2회(1차 4건·2차 2건 반영) → Codex 5시간 상한 54%로 Gemini argon-high 3차 APPROVE)
- 이유: 2단계 알림을 받은 글을 사람이 직접 고칠 필요 없이, 새 글과 같은 자료 찾기·사실 확인을 거친 갱신본을 승인 받아 같은 주소에 반영하려고.
- 사용법: 텔레그램 대화방에 "갱신 80"(또는 "글 80번 갱신", "80 갱신"; 본인 대화방·본인 계정 메시지만 인정) 또는 `.venv/bin/python scripts/jobctl.py refresh 80`.
- 설계(src/refresh.py)
  1. 갱신 작업 = source_ref "refresh:<글번호>:<시각>", checkpoint.target={post_id, link, title}(작업 등록과 같은 INSERT). 주제는 원래 제목에서 연도를 뺀 것, blog 모드(쇼츠 없음).
  2. 새 글과 똑같이 만들되 **따로 만든 임시 글**에 저장·캡처해 승인 요청(맨 앞 경고 "[갱신] 발행하면 글 N번 내용이 바뀝니다…").
  3. [발행] → wp.apply_refresh: 원래 글이 publish이고 임시 글이 draft·작업 표시 있음을 확인 후 제목·본문·요약·대표 사진·분류·태그만 원래 글에 덮어씀(slug·date·status는 보내지 않아 주소·작성일 그대로). 원래 글에 있던 옛 작업 표시는 본문 앞에 보존(fix_duplicate_thumbnails 등이 찾을 수 있게). 임시 글은 휴지통(실패해도 경고만, 갱신은 완료 처리). 이미 반영됐으면 임시 글 정리만(두 번 눌러도 안전).
  4. 작업 post_id는 임시 글 번호 유지(jobs_post_id 고유 충돌 방지), post_url은 원래 글 주소.
  5. 아침 8시 자동 발행·주제 자동 등록 대기 수에서 제외(사람이 누른 갱신만 반영). 하루 등록 3건(MAX_REFRESH_PER_DAY, 한국 날짜), 같은 글 진행 중 작업 있으면 거절.
  6. 중단 복구: reconcile_publishing이 원래 글의 작업 표시로 반영 여부 판단, 반영됐으면 임시 글 정리 후 완료 처리.
  7. 안전장치: jobctl retry/redo/redraft/cancel은 원래 글에 이 작업 표시가 있으면(공개 여부 무관, refresh_marked) 거절(redo가 원래 글이 쓰는 사진을 지우지 않게), 같은 글에 다른 갱신 진행 중이면 거절. 등록과 재작업은 같은 파일 잠금(data DB 옆 refresh.lock)으로 겹치지 않음.
- 파일: src/refresh.py(신규), tests/test_refresh.py(신규, Codex 작성 97개), src/wp_client.py(get_post_title·refresh_applied·refresh_marked·apply_refresh), src/orchestrator.py(발행·복구·경고·텔레그램 갱신 명령), src/telegram_notifier.py(RefreshCommand·메시지 받기), src/db.py(create_job checkpoint), src/daily_guard.py, src/topic_service.py, scripts/jobctl.py(refresh 명령·차단·잠금), src/freshness.py(알림 문구에 사용법).
- 검증: 전체 테스트 639개 중 638 통과(실패 1건은 기존 별건 test_topic_service::test_orchestrator_routes_topic_buttons). 11-19 첫 실제 갱신: 글 80번(청년도약계좌) → 작업 38번, 임시 글 216 승인·발행. 주소·작성일(11-09) 그대로, 수정일만 11-19, 제목·본문·대표 사진 교체, 옛 표시 보존, 216은 휴지통, 사이트 200 확인.
- 남은 일: 휴지통 이동 실패 시 남는 임시 글은 수동 정리, 원래 글을 만든 옛 작업의 사진은 갱신 후 안 쓰여도 남음(용량 작음, 정리 기능 없음), (선택) 2단계 알림의 '바뀔 항목'을 갱신 글쓰기에 힌트로 전달, ④(선택) 공식 출처 변경 감시. 텔레그램 '취소' 버튼은 작업 상태만 바꾸므로 잠금 대상 아님(사진 삭제 없음).

## 2026-11-19 갱신 글쓰기에 유효기간 장부의 '바뀔 항목' 전달 (지시: 사용자 / 작업: Claude, 시험 Gemini(Codex 5시간 상한) / 검토: Gemini argon(일반) APPROVE)
- 이유: 갱신 때 주제만 넘기면 어떤 정보가 낡았는지 글쓰기 AI가 모른다. 2단계 점검이 찾아 둔 '시간이 지나면 바뀌는 정보'를 짚어 주면 고칠 곳을 빠뜨리지 않는다.
- 동작: 갱신 등록(텔레그램 '갱신 N'·jobctl refresh) 때 freshness.checks_for가 장부(data/freshness.sqlite3 posts.items)의 항목을 'what (확인 이유: why)' 목록으로 읽어 checkpoint.target.checks에 저장 → 글쓰기 지시문에 "[다시 확인할 항목 - 예전 글 기준, 사실 근거 아님]" 블록으로 추가. 적힌 값은 예전 값이라 자료에서 최신 값을 찾고, 자료에 없으면 쓰지 않게 지시. 검토·사실 확인 단계에는 넘기지 않음(자료만 기준).
- 새 글·checks 없는 옛 갱신 작업은 지시문이 이전과 똑같다. 장부가 없거나 읽기 실패면 빈 목록으로 진행(경고 로그).
- 파일: templates/prompt_template.py, src/ai_generator.py, src/pipeline.py, src/orchestrator.py, src/refresh.py, src/freshness.py, scripts/jobctl.py, tests(test_refresh·test_freshness·test_content·test_orchestrator).
- 검증: 운영 장부에서 글 80번 3개·173번 2개 항목 읽힘, 없는 글은 빈 목록. 테스트 644개 중 643 통과(기존 별건 1건).

## 2026-11-19 오래 실패하던 시험 test_orchestrator_routes_topic_buttons 정리 (지시: 사용자 / 작업: Claude / 검토: 없음 — 시험 파일 한 줄)
- 원인: 시험이 주제 후보를 고정 날짜(2026-11-09)로 만들고, 버튼 처리(Orchestrator.handle_topic)는 실제 현재 시각으로 7일 기한을 판단 → 11-16 이후 '기한 지남'으로 실패. 프로그램 문제 아님.
- 수정: tests/test_topic_service.py 해당 시험만 후보를 현재 시각으로 생성. 전체 시험 644개 모두 통과.

## 2026-11-19 글 유효기간 점검 4단계: 근거 공식 페이지 변경 감시 (지시: 사용자 / 작업: Claude, 시험 Gemini argon-high(Codex 5시간 상한) / 검토: Gemini argon-high 1차 5건(P1 1·P2 3·P3 1) 반영 → 2차 APPROVE)
- 이유: 날짜 점검(2단계)은 '언제 바뀔 것 같은지' 예측이라 예고 없이 바뀐 금액·기준·기간을 놓친다. 글 쓸 때 읽은 공식 페이지를 다시 읽어 실제 변화를 잡는다.
- 사용자 결정(11-19): 하루 20곳 돌아가며 확인, AI가 '글에 영향 있음'으로 판단한 것만 알림, 첫 기준은 글 쓸 때 저장한 자료 본문.
- 동작(src/source_watch.py, 장부 data/source_watch.sqlite3): 실행기 스레드가 오후 2시 이후 하루 한 번
  1. WP에 지금 발행 중인 글마다 가장 최근 발행 작업(갱신 작업은 target.post_id)의 자료 중 via=direct만 감시. 키는 (주소, 글 번호) — 여러 글이 같은 페이지를 쓰면 기준·판정을 글마다 따로(페이지는 한 번만 읽음). 글을 갱신하면 그 작업 자료가 새 기준.
  2. 가장 오래 안 본 주소부터 20곳(days.fetched로 재시도 포함 하루 합계 20곳) 읽어 줄 비교. 40자 이하 줄의 조회수·수정일·담당자 등, 날짜만 있는 줄('~' 기간 줄은 남김), 4자 미만 줄, 순서 변화, 길이 제한(12,000자) 걸린 본문의 끝 20%는 무시.
  3. 바뀐 줄이 있으면 Gemini(structured)에 글 본문과 함께 '글 내용에 영향?' 판정 → changes 표에 영향 있음/무관 모두 기록(기준 조정용). 판정 실패면 기준 유지 후 다음 바퀴에 다시.
  4. 영향 있음만 텔레그램 한 통으로(글별 묶음 + "갱신 N" 안내). 길이 넘친 항목·보내기 실패는 다음 알림으로. 3번 연속 안 열린 페이지는 한 번만 알림. 우리 쪽 중계 장애는 실패로 세지 않음.
  5. '갱신 N' 때 이 글의 영향 있는 변화(현재 기준 작업 이후)가 freshness 항목과 함께 글쓰기 힌트로 들어감.
- 제외 결정: via=gemini(구글이 대신 읽은 페이지, 읽을 때마다 문장이 달라 비교 불가 — 복지로 등), via=law(법령 조문, 날짜 점검이 맡음).
- 수동: `.venv/bin/python scripts/source_watch.py show|run|check [--limit N]` (check = 알림 없이 확인, 알릴 것은 남겨 둠).
- 파일: src/source_watch.py·scripts/source_watch.py·tests/test_source_watch.py(신규, Gemini 작성 11개), src/orchestrator.py(스레드·힌트), scripts/jobctl.py(힌트). 커밋 65bdf9f.
- 검증: 전체 테스트 655개 통과. 11-19 운영 첫 실행(알림 없이 check): 감시 103곳(105줄), 20곳 중 19곳 읽음, 바뀜 3건 모두 '무관'(서울시 소식 2곳 상단 메뉴 개편, 국민연금 청년 지원 문구 변화 — 글 82번 실업급여 내용과 무관), 실패 1곳(고용24 실업급여 페이지가 메인으로 넘어감, 3회 연속이면 알림).
- 남은 일: 약 2주간 changes 표의 '무관' 판정을 보고 잡음 규칙 조정(`show`로 확인). 메뉴 개편처럼 사이트 전체가 바뀌면 그 사이트 글마다 AI 판정이 한 번씩 돈다(하루 최대 20건 수준이라 허용).
- 2026-11-20 예약 확인(국가장학금 작업 12): 한국장학재단 공지·정책브리핑에 2027학년도 1학기 국가장학금 1차 신청 공식 공고와 가~마 구간 단가 아직 없음(재단 공지는 11-19 국가근로장학금 신청 안내만). 비건뉴스 11-18 기사가 대학 공지(홍익대·서울과기대)를 근거로 '11-24 09시~2027-01-07 18시, 서류·동의 01-14 18시' 보도 → 공고 임박. 작업 12 보류 유지. 예약 trig_01StBKmtsbAdvLa7n2aqgaiE를 11-24 10:13 KST로 다시 걸음.

## 2026-11-20 첫 화면·분류 페이지 대표 주소(canonical)·공유 미리보기 (지시: 사용자 "ㅇ" / 작업: Claude / 검토: 없음 — 머리 태그만 추가, 되돌리기 쉬움)
- 이유: SEO 점검(11-18) 남은 항목. 워드프레스는 canonical을 글에만 넣어 첫 화면·분류 페이지는 ?utm 등 꼬리 붙은 주소와 구분이 안 됐고, 카톡 공유 시 사진이 없었다.
- 변경: deploy/wp/mu-plugins/jalcheck-seo.php → 서버 mu-plugins 반영(백업 data/backups/seo-canonical-20261120-131058). 첫 화면·분류: canonical(2쪽 이상은 자기 쪽 주소 /page/N/, 꼬리 제거). 1쪽만 og:image(사이트 아이콘 512px)·twitter:card summary, 분류는 og:title "분류명 - 잘체크"·og:url 추가.
- 검증: /, /page/2/, /category/benefits/, /category/benefits/page/2/, /?utm_source=x 모두 200·canonical 1개·올바른 주소, 글 페이지 canonical 1개(중복 없음), 소유 확인 태그·사이트맵 200 유지.
- 결정: 사진 파일 이름 바꾸기(SEO 점검 2번)는 하지 않음 — pixabay 일반 사진이라 이미지 검색 유입 기대가 낮고 글 생성 코드 수정 대비 효과 작음. 자체 제작 이미지를 쓰게 되면 그때.
- 남은 일: 공유 사진을 가로형(1200×630) 전용 이미지로 바꾸면 카톡 미리보기가 더 크게 보임(선택).

## 2026-11-21 대표 사진을 AI(Gemini)가 보고 고름 (지시: 사용자 "1" / 작업: Claude / 검토: Gemini hydrogen-high 경미 3건 반영)
- 이유: 발행 글 34개 대표 사진 중 13개가 주제와 어긋나거나(식당 영수증 등) 영어 큰 글씨·상표·얼굴이 보임. 애드센스 달기 전 상표·얼굴 사진을 줄이려는 목적도 있음. 글자 썸네일은 11-08 결정대로 쓰지 않음.
- 동작: 첫(대표) 사진만 Pixabay 후보 최대 4장(다른 글이 쓴 사진·연작 제외)을 작은 미리보기(webformatURL)로 받아 `src/image_judge.py`의 Gemini(`gemini_model`)가 주제 적합·상표·식별 가능한 얼굴(어려운 처지 주제)·큰 외국어 글자를 보고 1장 고름 → 고른 사진만 큰 파일로 받음. 본문 사진은 예전 그대로.
- 되돌아가기: 판정 오류·한도·이상한 응답·전부 부적합(-1)·후보 1장 이하면 예전 방식(검색 순서 첫 후보). 글 작성은 멈추지 않음. 비용 글당 판정 1번(약 1원).
- 파일: src/image_judge.py(새), src/stock_media.py(`_judged_image`, `pick_blog_images(judge, topic)`), src/pipeline.py(`Services.judge`), tests/test_image_judge.py, tests/test_stock_media.py.
- 검증: 전체 시험 664 통과. 최근 글 9개 실제 시험: 7개 개선(영어 "Business"/"CREDIT CARD" 글씨·얼굴 보이는 졸업생 사진 → 글씨·얼굴 없는 사진), 1개 같은 사진, 1개(전기요금 할인) 후보 전부 부적합으로 예전 방식.
- 한계·남은 일: 검색 후보가 처음부터 엉뚱하면 못 고침 → 사진 검색어 개선(2번, 보류). 이미 올린 글의 부적합 대표 사진 교체(광고 전), youtube_kit "정보 기준: … 기준" 중복.

## 2026-11-21 이미 올린 글의 대표 사진 AI 재판정·교체 (지시: 사용자 "1" / 작업: Claude / 검토: Gemini neon-high 위험 리뷰 → 지적 4건 반영 후 APPROVE)
- 이유: 광고 달기 전, 11-21 이전 글의 대표 사진 중 주제와 어긋나거나(스페인 식당 영수증, "STEUERN" 글씨 등) 상표·얼굴이 보이는 사진 정리.
- 도구: `scripts/rejudge_featured.py <계획.json>`(판정만, 워드프레스 안 건드림, 비교 그림 생성) → 사람이 확인 → `--apply`(계획대로 대표 사진만 교체, 옛 사진은 안 쓰이면 삭제). 지금 사진을 0번으로 함께 보여 줘서(`_judged_image(incumbent=)`) 지금 사진이 가장 나으면 그대로 둠.
- 적용 전 재확인: 진행·대기 작업 있으면 멈춤, 작업 상태·글 번호·장부 사진·워드프레스 대표 사진(`old_media`)이 계획 때와 같은지, 새 사진을 다른 글이 썼는지, 휴지통·갱신 글·본문에 대표 사진이 든 글은 건너뜀. 글별 예외 격리.
- 결과: 12개 글 교체(작업 1,3,7,8,13,15,19,20,24,25,29,31 / 글 35,57,73,76,132,137,149,152,171,173,181,186, 워드프레스 사진 233~244). 6개는 나은 후보 없음, 나머지는 지금 사진이 가장 나음으로 유지.
- 건너뜀: 작업 34~42(11-17 이후 글은 대표 사진이 본문 첫 사진과 같은 파일 → 대표만 바꾸면 이름이 겹침). 본문 사진까지 함께 바꾸는 별도 작업 필요(미정).
- 백업: `~/backups/rejudge-20261121-0501/`(wp.sql.gz, jobs.sqlite3).
- 검증: 전체 시험 665 통과, 12개 새 사진 https 200, 홈 200, 장부(media.blog) 새 사진 번호 반영 확인.
- 함께 바꿈: `src/image_judge.py` 응답 길이 1024→4096(JSON 잘림 1회 발생).

## 2026-11-21 11-17 이후 글: 대표 사진 + 본문 같은 사진 함께 교체 (지시: 사용자 "1" / 작업: Claude / 검토: Gemini neon-high 위험 리뷰 → 지적 4건 반영 후 APPROVE)
- 이유: 11-17 이후 글은 대표 사진이 본문 첫 사진과 같은 파일이라 대표만 바꾸면 이름이 겹침(위 항목에서 건너뛴 작업 34~42).
- 변경: `WordPressClient.replace_featured(body_figure=...)` — 본문에서 옛 사진 묶음(<figure>)을 정확히 한 곳에서 찾으면(`_old_figure`: 이름 1회, wp-image 옛 번호, figure 1개, 블록 주석 없음) 새 사진 묶음(blog_html `_image`, make_draft와 같은 모양·출처 글)으로 바꿔 대표 사진과 한 요청으로 저장. 저장 전 새 묶음 검증, 저장 후 대표·새 주소·옛 묶음/옛 wp-image 사라짐 확인. 장부 기록 실패 시 옛 사진 안 지움, 옛 사진 삭제 실패는 경고만. 오류 문구에 새·옛 첨부 번호.
- 스크립트: 계획에 `in_body` 기록·적용 때 재확인, `--jobs`(판정·적용 모두 거름), 마커가 2개 이상인 글(갱신 흔적) 제외.
- 결과: AI 판정 8개(38은 갱신 작업이라 제외) → 바꿈 5개 중 양도소득세(42)는 Claude가 비교 그림 보고 뺌(검은 장갑 손 사진, 세금 글에 어두움). 적용 4개: 작업 34(글 205, 247), 35(글 208, 249), 36(글 211 승인 대기, 251), 40(글 224, 245). 37·41은 지금 사진 유지, 39는 나은 후보 없음.
- 백업: `~/backups/rejudge2-20261121-0522/`.
- 검증: 시험 670 통과. 40번 먼저 1개 적용 후 실제 화면 확인(og:image·본문 사진·srcset·출처 글 새 사진, 옛 파일 404) → 나머지 3개. 4개 모두 API로 대표·본문 wp-image·장부 일치 확인, 홈 200.

## 2026-11-21 대표 사진 후보가 전부 부적합일 때 다른 검색어로 다시 판정
- 지시: 사용자("사진 검색어 다듬기" 선택). 작업: Claude. 검토: Gemini(gemini-4-argon-medium, 1차 수정 요청 → 반영 후 승인).
- 왜: (a) 사진 검색어(image_query)가 하나도 없는 글(작업 9·22·23·27·28·29·30·39 등)은 대표 사진을 쇼츠 첫 장면 검색어로 AI 판정 없이 골랐다. (b) 첫 검색어 후보 4장이 모두 부적합(AI가 -1)이면 다시 찾지 않고 검색 순서 첫 사진을 썼다(예: 전기요금, 국민취업지원제도).
- 글쓰기 프롬프트(검색어 만드는 지시)는 고치지 않았다: 다른 대화가 ai_generator·content_schema를 고치는 중이고 본문 사진에도 영향이 가서. 사진 고르는 코드에서만 고쳤다.
- 바꾼 것: `StockMediaPicker.judged_featured`(src/stock_media.py) — 첫 검색어 → 뒤 소제목 검색어 → 쇼츠 장면 검색어 차례로, 같은 검색어는 한 번, AI 판정은 글마다 최대 3번(`JUDGE_QUERY_TRIES`, 판정 1번 약 1원). 후보가 1장 이하라 판정 안 한 검색어는 안 셈. 판정 오류(한도·통신)는 바로 멈추고 예전 방식. `pick_blog_images`는 글마다 판정 묶음을 한 번만 돈다. `pipeline.pick_media`는 사진 검색어가 하나도 없을 때 장면 검색어로 AI 판정.
- 알려진 한계: 다른 검색어에서 고른 사진이 첫 소제목 자리에 들어가므로 그 소제목의 image_alt와 딱 맞지 않을 수 있다. 3번 모두 부적합이면 예전 방식(첫 검색어 첫 사진)으로 고른다(예: 작업 30 도시가스는 후보가 공장 배관·책뿐이라 개선 안 됨).
- 검증: 테스트 675개 통과(새 테스트 5개). 실제 시험(발행 안 함): 작업 39 → 노트·펜 책상 사진, 9 → 운전대 사진, 22 → 휠체어 뒷모습, 30 → 3번 모두 부적합(예전 방식).
- 이미 올린 글에는 적용하지 않음(새 글부터). 필요하면 scripts/rejudge_featured.py로 다시 판정 가능.

## 2026-11-21 [발행] 순서 확인: 더 오래된 승인 대기 글이 있으면 한 번 더 묻기
- 지시: 사용자("1"). 작업: Claude. 검토: Gemini(gemini-4-argon-medium) APPROVED.
- 왜: 작업 40을 11-20 밤 텔레그램 [발행]으로 먼저 올려서 37보다 뒤 번호가 먼저 발행됨(아침 자동 발행은 가장 오래된 글 하루 1편). 번호가 뒤섞이면 헷갈림.
- 결정: "가장 오래된 글만 발행 가능"은 사실 확인 보류 글이 맨 앞이면 전부 막히고 급한 글을 못 올려서 채택 안 함. 대신 순서가 아니면 묻는다.
- 바꾼 것: `Orchestrator.handle_decision` — 사람이 [발행]을 눌렀는데 더 오래된 대기 글(사실 확인 보류·갱신 작업 제외)이 있으면 발행하지 않고 그 글의 승인 요청에 답장으로 [N번 먼저 발행]/[그래도 M번 발행] 버튼을 보냄(`older_waiting`, `ask_order`, 새 버튼 코드 `f`=force). 아침 자동 발행(`daily_guard.AUTO_CALLBACK`)과 갱신 작업 자체는 확인 안 함. 발행 결과는 항상 원래 승인 요청 메시지에 달고, 순서 확인 메시지에서 눌렀으면 그 메시지 버튼만 지움. 버튼 발행도 실행기 기록(`작업 N번 발행(버튼)`)에 남김(전에는 기록이 없어 40번 발행 경위를 기록으로 확인 못 함).
- 검증: 테스트 681개 통과(새 테스트 6개).

## 2026-11-21 텔레그램 맨 아래에 항상 [다음 승인 대기 글 보기] 버튼
- 지시: 사용자("항상 마지막에 다음 승인 대기글 버튼"). 작업: Claude. 검토: Gemini argon-medium 1차 지적 3건(승인 요청 영상 업로드 중 끼어들기, 확인 주기 부족, Session 공유) 중 2건 반영 → APPROVED. Session 공유는 이전부터 있던 구조라 범위 밖.
- 왜: 다음 글 버튼 뒤로 새 승인 요청·알림·[다음 글] 이동 안내가 붙으면 버튼이 위로 밀려 찾아 올라가야 했음.
- 바꾼 것: `TelegramNotifier`가 send* 때 맨 아래 메시지에 n: 버튼이 있는지 기억(`_bottom`, 재시작하면 잊음). `sending()` 묶음(승인 요청 캡처·영상·본문) 중엔 끼우지 않음. `Orchestrator.keep_next_at_bottom` — 전용 스레드 `bottom-next`가 5초마다, 맨 아래에 버튼이 없고(마지막 전송 3초 뒤) 대기 글이 있으면 "다음 승인 대기 글로 가려면…" + 버튼(가장 오래된 글부터) 전송. 버튼 처리·아침 자동 발행·발행 확인 정리 중엔 `_BOTTOM_LOCK`으로 건너뜀. [다음 글] 이동 안내와 순서 확인 메시지에도 다음 글 버튼을 직접 붙임.
- 알려진 점: 알림 하나 뒤에 버튼 메시지가 하나 더 붙음(대기 글이 있을 때만). 예전 버튼은 남아 있어도 정상 동작.
- 검증: 테스트 686개 통과(새 4개).

## 2026-11-21 근거 변경 → 고친 글 자동 등록 + 확실한 것만 다음 날 아침 자동 반영
- 지시: 사용자("최대한 자동화", "2. 대신 확실한 정보만 바꿀 수 있도록", "ㅇ"). 작업: Claude. 테스트: Gemini hydrogen-high. 검토: Codex gpt-6-astra 1차 6건 → 반영, Gemini neon-high 2차 P1 3건 → 반영 후 APPROVE.
- 왜: [근거 변경] 알림이 와도 사용자가 "갱신 N"을 직접 입력해야 했음.
- 흐름: source_watch.notify가 알림 때 글마다 `refresh.request_refresh(source="cli", auto={urls, change_ids, changes})`로 고치는 작업 자동 등록(하루 3건 제한·같은 글 진행 중 막기 그대로). 이미 등록에 쓴 변화(change_ids, 그 작업을 취소했어도)는 다시 등록 안 함. 새 글은 바뀐 공식 페이지를 먼저 읽음(pipeline.make_content must_read).
- 자동 반영 기준(src/auto_refresh.py evaluate, 모두 통과해야): ① 사실 확인 문장 모두 근거 있음(hold·failed 아님) ② 바뀐 공식 페이지를 직접 읽음(via=direct) ③ 예비 모델 작성 아님·경고 0건 ④ 예전 글과 AI 비교(제목·본문·글 속 링크 주소, 1순위 모델만 인정)에서 공식 페이지 변화로 설명 안 되는 삭제·변경·추가 0건. 글이 3만 자를 넘거나 비교 오류면 거부.
- 통과하면 승인 요청에 "[자동 반영 예정]"과 바뀐 내용, 다음 날 오전 8시 DailyGuard.apply_refreshes가 [발행]과 같은 처리로 원래 글에 덮어씀. 그 전에 판정 때 지문(제목+본문 sha256)과 원래 글·임시 글이 같은지 확인, 다르면 반영 안 하고 알림. 시도는 한 번뿐(장부 auto_apply.attempted), 실패하면 버튼으로만.
- [보류]를 누르면 held — 다시 써도(redraft) 유지, 자동 반영 안 함. [취소]도 막힘. 통과 못 하면 "[자동 반영 안 함] 이유"를 보여 주고 사람이 [발행].
- 관찰: 기준이 엄격해 처음엔 수동으로 넘어가는 경우가 많을 수 있음. 2주(~12-05) 지켜보고 자주 막히는 사유를 보고 조정.
- 범위 밖: 화면 캡처 실패 경고(글 내용과 무관), jobctl redraft의 검토 노트 유실(기존 동작).
- 파일: src/auto_refresh.py(새), src/refresh.py, src/pipeline.py, src/orchestrator.py, src/daily_guard.py, src/source_watch.py, tests/test_auto_refresh.py(44개).
- 검증: 전체 테스트 730개 통과. 실제 알림·반영은 아직 안 일어남(다음 근거 변경 알림 때 확인).
- 첫 실제 사례(11-21): 글 173(월세 세액공제) 변화 16번으로 작업 43 자동 등록 → 자동 반영 안 함(경고 1건: '자료에서 못 찾은 숫자 170만 원' — 한도 1,000만×17% 계산값이라 자료 원문에 없음). 계산으로 나온 숫자 경고가 자주 막으면 2주 관찰 때 조정 후보.

### 11-21 추가: 숫자 경고는 AI가 자료와 대조 + 예전 글 비교 2회
- 지시자: 사용자("나는 발행만 누르고 판단은 AI가") / 작업자: Claude(오라클2) / 테스트 작성: Gemini hydrogen-high / 검토: Gemini neon-high 5회(Codex 5시간 한도 초과로 대체), 최종 APPROVE.
- 숫자 경고('자료에서 못 찾은 숫자')만 AI가 공식 자료와 대조해 숫자별로 맞음/틀림/확인 불가 판정. 전부 '맞음'일 때만 통과(계산값도 자료에서 단순 계산되면 맞음). 다른 종류 경고는 여전히 자동 반영 안 함(사람 판단).
- AI는 판정만 하고 글을 고치지 않음(틀린 글을 AI가 다시 쓰면 또 검증이 필요해 자동 반영 안전성이 떨어짐). 틀리면 사유를 알림에 적고 사람이 처리.
- 안전장치: 자료가 80,000자 넘으면 일부만 보내지 않고 거부, 예비 모델 판정은 거부, 숫자 번호(id)로 1:1 대조, 자료·글을 울타리(_fence)로 감싸고 그 안의 지시는 무시하도록 지시, 천 단위 쉼표 숫자 분리 오류 수정.
- 예전 글 비교를 2번(COMPARE_RUNS=2) 해 차이를 합침: 실제로 첫 비교가 빠진 문단을 놓친 일이 있었음.
- 작업 43(초안 글 255) 수동 복구 4건: 오해 소지 문장(최대 170만 원) 정리, 홈택스 링크·'개편 예정' 청년 17% 안내·결정세액 주의 문단·뉴시스 출처 되살림. 백업 ~/backups/post255-before{,2,3,4}-20261121.html. 고친 뒤 실제 판정 3회 연속 통과 → 사용자 [발행] 대기.
- 파일: src/auto_refresh.py, tests/test_auto_refresh.py. 검증: 전체 테스트 754개 통과.

## 2026-11-21 자동 확인 개선 ③-A: 글 쓰기 전 최근 확정 변경(개편) 확인
- 지시: 사용자 / 작업: Claude / 검토: Gemini(gemini-4-neon-high, 지적 8건 대조 후 전부 반영)
- 이유: 공식 안내 페이지는 법이 바뀐 뒤 늦게 고쳐지는 일이 많아, 확정된 개편을 글에 반영하고 논의 중인 개편안은 사람이 참고만 하게 하려는 것.
- 흐름(src/recent_change.py, pipeline.make_content의 법령 다음): 네이버 뉴스 "{제도이름} 개편/개정/달라지는" 검색 → 1년 안·제도이름 포함 기사(korea.kr 우선) 최대 3건 읽기 → Gemini 1회(Generator.read_changes)로 변경 문장·확정 근거 문장 인용 → 프로그램이 다시 확인: 변경 문장이 기사에 있는지, 근거가 기사 문장 전체로 '통과했다·공포됐다·시행 중'이고 '하면·예정·검토·상임위·제출·돼야' 등이 없는지, 변경 문장에 조건 표현이 없는지.
- 확정만 기사 인용 그대로 Source(via="news", 종류="최근 확정 변경")로 추가(감시·링크 점검 대상 아님). 지시문 CHANGE_NOTE: 시행일 미래면 현행+"○월부터 바뀝니다", 지났으면 새 값+"○월 시행된 개정 기준"(unverified에 안 적음, 리뷰 4번과 충돌 방지), 법령·더 새 공식 안내와 다르면 그쪽 우선, '내년' 등은 기사 날짜 기준.
- 승인 알림 "자동 검토에서 고치고 확인한 것" 맨 앞에 "최근 변경(확정) 반영: …" / "최근 변경(논의 중, 글에 안 씀): …"(논의 중은 기사당 1줄, 최대 4줄). "확인할 것"에는 안 넣음. 모든 실패는 건너뛰고 글은 계속 작성. 기록은 결과 recent_changes.
- 사용량: 글 1편당 뉴스 검색 3회 + 기사 읽기 최대 3건 + Gemini 1회(기사 있을 때만).
- 실제 시험: 월세·부모급여·생계급여·K-패스 → 논의 중 개편안만 잡힘(정상), 육아휴직 → 없음. 확정 사례는 아직 실제로 못 봐서 단위 테스트로만 검증.
- 파일: src/recent_change.py(신규), src/ai_generator.py, src/pipeline.py, templates/prompt_template.py, tests/test_recent_change.py(11개). 전체 테스트 765개 통과. 커밋 8874ca7, worker 재시작·사이트 200 확인.
- 남은 일: 실제 확정 사례가 글에 어떻게 반영되는지 관찰. K-패스 글에서 전국 공식 페이지 대신 경기·인천 페이지가 자료로 잡히는 문제(별개, fact_check 보류 1건 원인).

## 2026-11-22 홈 글 목록 페이지 나누기(SEO)
- 지시: 사용자(선택지 1번) / 작업: Claude(오라클2) / 검토: Gemini hydrogen-medium(지적 2건 중 1건 반영)
- 이유: 홈은 최신 10개만 보이고 더 보기가 없었음. 게다가 /page/2/ 주소가 1쪽과 같은 글을 보여 주면서 자기 주소를 대표 주소(canonical)로 표시해 중복 페이지가 될 수 있었음. 구글 권장대로 쪽마다 고유 주소·실제 링크(페이지 번호)로 나눔. '더 보기' 버튼(자바스크립트로 이어 붙이기)은 구글이 못 눌러 제외.
- 동작: 1쪽 = 큰 글 1개 + 카드 9개, 2쪽부터 = 큰 글 칸 없이 다음 글부터 카드 9개씩. 카드 목록(home.html jc-card-query)을 주 목록(inherit)으로 바꾸고 페이지 번호 블록 추가, 새 mu-plugin jalcheck-home.php가 offset(1+(쪽-1)×9)·쪽수 보정(found_posts-1)·2쪽부터 큰 글 칸 생략(pre_render_block)을 맡음. 피드·관리자는 제외.
- 주의: 주 목록에만 조건(예: 카테고리 제외)을 추가하면 큰 글 쿼리와 어긋나 중복·누락될 수 있음 → 바꿀 땐 home.html 큰 글 쿼리도 같이.
- 파일: deploy/wp/mu-plugins/jalcheck-home.php(새), deploy/wp/templates/home.html. 반영: apply_site_pages.py(백업 data/backups/site-pages-20261122-032444).
- 검증(실서버): 1~5쪽 카드 38개 + 큰 글 1개 = 39개 전부, 중복 0, 6쪽 404, 쪽별 canonical 자기 주소, 피드 10개 정상.

## 2026-11-22 공식 자료 찾기 개선(K-패스 글에 경기·인천 페이지가 자료로 잡히던 문제)
- 지시: 사용자 / 작업: Claude / 검토: Gemini(gemini-4-neon-high, 지적 5건 반영)
- 원인 3가지: ① 국토교통부는 첫 방문에 쿠키를 주고 같은 주소로 307 → 쿠키를 안 돌려줘 '리다이렉트가 너무 많음' ② K-패스 운영 기관 kotsa.or.kr이 공식 목록에 없음, 전국 사이트 korea-pass.kr과 부산시청은 이 서버 접속을 끊음(Connection reset, 고칠 수 없음) ③ 검색 순서대로라 경기·인천 지역 사업 페이지가 앞에 옴.
- 수정: source_fetcher._fetch가 한 번의 읽기 안에서 받은 쿠키를 같은 (scheme, host)에만 돌려줌(제어문자·공백 든 쿠키는 버림, 내부 주소 검사는 매 단계 그대로). source_finder: kotsa.or.kr 추가, 시·도청(LOCAL_HOSTS) 페이지 중 검색 제목에 제도 이름(core_keyword)이 없는 것만 뒤로(기후동행카드처럼 지역 제도면 그대로). 처음엔 '주제에 지역 이름이 있나'로 판단했으나 '경기 침체' 오탐·지역명 없는 지역 제도 문제로 제목 기준으로 바꿈. 기초 단체(시청·구청)는 이름 규칙이 smartcity.go.kr 같은 중앙 포털과 겹쳐 판별하지 않음.
- 검증: 테스트 767개 통과. K-패스 make_content 재실행 → 자료 부산(K-패스)·교통안전공단·과천(K-패스 기반)·온통청년, 사실 확인 보류 1건 → 0건. 커밋 6636034, worker 재시작·사이트 200.
- 남은 일: 같은 시험에서 '자료에서 못 찾은 숫자: 3.5만, 7만, 3만, 6.5만'(모두의카드 기준금액) 경고 1건. 국토부 모두의카드 페이지는 이제 읽히지만 자료 4개 한도에 밀림, 국토부 보도자료 본문은 자바스크립트라 못 읽음.
- 11-22 추가(사용자 지적: 홈 카드 마지막 줄 빈칸): 카드 격자가 화면 너비에 따라 한 줄 1~4개(auto-fill 260px, 넓은 폭 1340px)라 9개면 4칸 화면에서 마지막 줄 1개. 홈 카드 9→12개(1·2·3·4로 다 나눠짐), 워드프레스 설정 posts_per_page 10→12(카테고리·검색 화면도 10개라 같은 빈칸, 템플릿 perPage 12는 inherit라 무시되고 있었음. 원래 값 10). 확인: 홈 1~3쪽 12개·4쪽 2개, 카테고리 12개, 쪽 제목에 '페이지 N' 붙어 제목 중복 없음. 마지막 쪽은 글 수에 따라 덜 찰 수 있음(정상).

## 2026-11-22 새 글 '못 찾은 숫자' 경고를 AI가 자료와 대조
- 지시: 사용자("1", 금액 경고 해결) / 작업: 오라클2 Claude / 검토: Gemini(gemini-4-neon-high, 조건부 통과 → 지적 반영)
- 원인: K-패스 글의 "3.5만, 7만, 3만, 6.5만" 경고는 오탐. 교통안전공단 표가 '만 원' 단위 없이 "3.5 7"처럼 숫자만 적어 프로그램 숫자 검사가 못 맞춤. 단위 없는 작은 숫자를 프로그램이 일괄 인정하면 진짜 틀린 숫자도 놓치므로 프로그램 규칙은 그대로 두고 AI 대조를 붙임.
- 수정: `pipeline.verify_figures` — make_content 끝에 '자료에서 못 찾은 숫자' 경고가 있을 때만 1순위 모델로 1회 대조(auto_refresh.check_warnings 재사용). ok 숫자는 경고에서 빼 fixes에 "숫자 자료 대조 확인(AI): 숫자(근거)"로, 틀림·확인 불가는 경고에 남기고 근거를 한 줄("AI 대조: …")로. 오류·자료 과다·예비 모델 판정·예비 모델로 쓴 글이면 원래 경고 유지.
- 안전장치: 경고에서 뺀 숫자는 `cleared_figures`로 남겨 갱신 자동 반영(figure_list)이 한 번 더 대조(사람 없이 반영할 때 한 번의 판정만 믿지 않게). "AI 대조:" 경고 줄은 static_reasons가 자동 반영을 막음.
- 검증: 테스트 772개 통과(tests/test_verify_figures.py 5개 추가). 실제 K-패스 자료로 대조 → 4개 모두 ok("교통안전공단 표에 3.5(만 원)" 등 정확한 근거), 경고 0건.
- 사용량: 숫자 경고가 있는 글만 Gemini 1회 추가.
- 11-22 결정(사용자): 폰 화면은 카드 한 줄 1개(큰 사진) 그대로 유지. 12개라 스크롤이 긴 편이므로, 사용자가 길다고 느끼면 폰에서만 '왼쪽 작은 사진 + 오른쪽 제목' 촘촘한 목록으로 바꾸기로(CSS만, 글·링크 동일해 SEO 영향 없음).

## 2026-11-22 업로드 안내에 네이버 클립 설명 추가
- 지시자: 사용자("1": 네이버 클립부터 직접 몇 편 올려 보기, 아내 제안) / 작업자: Claude / 검토: 작은 변경이라 Claude가 테스트로 확인
- 이유: 같은 쇼츠 영상을 네이버 클립에도 올려 반응을 2~3주 보기로 함. 클립은 외부 자동 업로드 방법이 공개돼 있지 않아 사용자가 앱에서 직접 올리고, 복사할 설명만 텔레그램으로 보냄. 릴스는 보류
- 변경: src/youtube_kit.py(UploadKit.clip_text, clip_text(): 쇼츠 제목 첫 줄(클립은 제목 칸 없음)·검색 설명·정보 기준·글 주소(11-22 추가: 눌리지 않아도 복사해 들어갈 수 있게. 처음엔 안 눌릴 것이라 추정해 뺐으나 확인된 근거 없었음)·확인 안내·음악 출처(CC BY 4.0, 플랫폼마다 필요)·해시태그), src/orchestrator.py send_upload_kit(영상 → 유튜브 제목 → 유튜브 설명 → 클립 설명, 다음 글 버튼은 마지막 메시지로 이동)
- 테스트: tests/test_orchestrator.py 업로드 안내 4개 갱신. 기존 test_upload_kit_still_sent_when_number_warning_fails_to_send는 가짜 send_text가 reply_markup을 못 받아 설명 전송이 실패하는데도 우연히 통과하던 것을 바로잡음. 전체 772 통과
- 남은 일: 2~3주 뒤 클립 반응 보고 계속·릴스 추가 여부 결정. 이미 발행된 글의 쇼츠는 클립 설명이 안 감(필요하면 따로 생성)
- 2026-11-22 사용자 캡처로 클립 업로드 화면 확인(clipcreators.naver.com): 제목 칸 없음·설명 1칸(최대 3,000자) → 첫 줄 제목 방식 맞음. '콘텐츠 링크'는 네이버 블로그·네이버TV·뉴스만 가능 → 외부 글은 설명 속 주소로만. 'AI 활용 설정' 있음 → 우리 쇼츠(AI 대본·Gemini 음성)는 켜도록 안내. 설명 속 주소가 눌리는지는 아직 미확인

## 2026-11-23 주제 자동 등록: 기준 8편·하루 5편·하루 두 번(9시·15시)
- 지시: 사용자("1") / 작업: 오라클2 Claude(코드), Codex gpt-6.1-terra(테스트 수정·추가) / 검토: 작은 숫자·회차 변경이라 Claude가 diff와 테스트로 확인
- 이유: 사용자가 하루 3~4편 올릴 예정. 예전 하루 상한 3편은 소비량보다 작아 대기 글이 매일 줄고, 9시 한 번만 채워 낮에 승인하면 다음 날 9시까지 빔(11-23 오후 대기 2편). 9시에 몰아 써도 작업기가 한 편씩 차례로 써서(11-22 3편 약 15분) 부하 문제는 없었음 → 시간대를 나누는 이유는 부하가 아니라 오후 보충.
- 변경: src/topic_service.py(AUTO_HOURS=(9,15), AUTO_TARGET 5→8, AUTO_MAX_PER_DAY 3→5는 하루 모든 회차 합계, 성공 1번·실패 시 1시간 뒤 1번 재시도를 회차마다), src/topic_store.py(auto_registered_today: 오늘 자동 회차가 넣은 글 수), src/orchestrator.py 주석, tests/test_topic_auto.py(오후 보충·하루 합계 상한·같은 회차 반복 안 함·오전 실패 뒤 오후 재시도 추가)
- 검증: 전체 테스트 776개 통과. jalcheck-worker 재시작.
- 참고: 주제 후보가 부족하면 "찾지 못했습니다" 알림이 하루 두 번 올 수 있음.

## 2026-11-23 기본 제도 목록 42 → 90개 (1단계 분야 주거·연금 포함)
- 지시: 사용자("ㅇ") / 작업: 오라클2 Claude / 검토: Gemini gemini-4-neon-high(11건 지적 → 명칭 변경은 원래 이름 유지 + 새 이름을 '다른 이름'에, 검색어 4건 교체, 간이과세자 달 1월로)
- 이유: 기존 42개를 모두 써서 15시 자동 등록이 "후보 없음". 1월이 되면 기존 42개가 새 연도 글 후보로 돌아오지만 12월이 빔.
- 변경: templates/topic_seeds.txt에 48개 추가(지원금·복지 22, 주거 6, 연금·노후 6, 건강보험 3, 세금 9, 절약 2). 기존 글과 이름 겹침 0, 형식 검사·테스트 776 통과. 실행기는 회차마다 파일을 새로 읽어 재시작 불필요.
- 분야 칸은 텔레그램 표시용이라 기존 3개(지원금·세금·절약) 안에서 고름. 워드프레스 카테고리는 글 쓸 때 AI가 기존 카테고리에서 고름 → 주거·연금 글이 3편쯤 모이면 카테고리 추가(11-08 원칙).

## 다음 작업(사용자 승인됨, 설계 → Codex 검토 → 반영): 뉴스에서 찾은 주제 자동 등록
사용자 결정(11-23):
1. 뉴스 주제 글도 자동 발행 가능하게. 단 추가 조건(공식 사이트 자료 2곳 이상, 숫자 경고 0, 글 쓴 AI와 다른 회사 AI가 '실제 있고 지금 신청 가능한 제도인지' 확인)을 하나라도 못 넘으면 그 글만 버튼 승인으로, 텔레그램에 이유 표시.
2. 지역 제도 허용, 우선순위: 전국 → 인구 많은 광역(서울·경기·부산·인천 등) 대표 제도 → 시·군·구는 검색 많거나 그 지역만의 특색 제도만. 같은 종류를 지역별로 찍어내지 말고 '지역별 비교' 한 편으로 묶기(저품질 판정 위험). 제목에 지역명, 지역은 태그.
3. 신청 기간 끝난 제도 제외.
4. 범위 단계 확장 찬성: 1단계 주거·연금 → 2단계 건강·의료·정책 금융상품. 새 분야 첫 3편은 자동 발행 말고 직접 확인.
5. 그 밖: 주관 기관(정부·지자체·공단) 확인으로 광고성 기사 제외, 기존 글과 같은 제도인지 AI로 확인, 찾은 제도는 기본 목록에 자동 추가(다음 해 새 연도 글로 이어짐), '주제 못 찾음' 알림은 하루 한 번만.

## 2026-11-23 Gemini API 호출 낭비 줄이기(최적화 1~3)
- 지시: 사용자("1": 최적화 1~3 먼저 → 뉴스 주제 자동 등록 → 4·5는 확인 뒤) / 작업: 오라클2 Claude / 조사·검토: Gemini neon-high(호출 전수 조사, 리뷰 CHANGES_REQUESTED 4건 → 전부 반영)
- 조사 결과: 외부 AI는 Gemini API뿐(GPT 호출 없음). 새 글 1편 정상 8~10회(글쓰기·검토·사실 확인·숫자 대조·법령 고르기·최근 변경·음성·사진·분류), 하루 약 35~70회 추정. 가장 무거운 건 횟수보다 같은 자료 원문(최대 약 7만 자)을 한 글에서 3~7번 보내는 것.
- 의도적 중복(안 줄임): review와 fact_check 분리, verify_figures + 자동 반영 때 숫자 재대조, COMPARE_RUNS=2.
- ① auto_refresh 판정(compare·check_warnings, verify_figures 포함)은 structured(main_only=True)로 1순위 모델만 호출. 예전엔 1순위 실패 시 2·3순위까지 불러 놓고 결과를 버림. 혼잡이면 "1순위 모델이 혼잡·한도로 판정하지 못했습니다"로 자동 반영 안 함.
- ② pipeline.gather_sources로 자료 모으기 분리, 체크포인트 gathered에 저장 → Gemini 혼잡 재시도 때 같은 주제·같은 must_read·24시간 안이면 재사용(페이지 읽기·법령 고르기·최근 변경 확인 반복 안 함). 법령 고르기·최근 변경 판정이 실패한 흔적이 있으면 재사용 안 함(혼잡 때 빈 결과가 굳는 것 방지). 글 저장 뒤 지움, jobctl redo도 지움.
- ③ jobctl redraft 때 이전 분류(경고 없을 때만)를 kept_category로 남겨 분류를 다시 고르지 않음(워드프레스 분류 유지). draft 저장 뒤 지움. 자동 반영 판정 재실행은 redraft로 글 지문이 바뀌므로 필요한 동작이라 그대로.
- 파일: src/ai_generator.py, src/auto_refresh.py, src/pipeline.py, src/orchestrator.py, scripts/jobctl.py, tests(content·orchestrator·auto_refresh). 커밋 6aa2311, 8ed5239, cd5e61a(분류), e4b3b6d.
- 검증: 전체 테스트 782개 통과. jalcheck-worker 재시작·사이트 200.
- 남은 최적화: ④ 같은 자료 반복 전송 — Gemini 자동 캐시(앞부분이 같은 요청 할인)가 우리 모델에서 되는지 확인 후 자료를 요청 맨 앞에 같은 순서로 ⑤ 분류 고르기·주제 문장 다듬기처럼 결과를 프로그램이 다시 검사하는 쉬운 일은 가벼운 모델로. 뉴스 주제 자동 등록 뒤 진행.
- 뉴스 주제 설계 추가 결정(사용자): Codex를 못 쓸 때(5시간 한도 50%↑·오류)는 버튼 승인 대신 Gemini가 확인 — 글 쓴 모델(3.8-flash)과 다른 강한 모델(agy CLI gemini-4-neon-high, 구독 사용량)로. 그것도 실패하면 버튼 승인. 지역별 비교 글은 자동 등록하지 않고 월요일 후보로만(설계 1안).

## 2026-11-23 뉴스에서 찾은 주제 자동 등록(2단계)
- 지시: 사용자(위 결정 1~5, 설계 1안 승인, Codex 못 쓰면 Gemini 확인) / 작업: 오라클2 Claude / 검토: Codex gpt-6-astra high 2회(1차 5건 중 4건 반영, 2차 3건 중 2건 반영)
- 주제 찾기(src/topic_finder.py): 뉴스 발견에 지역 제도(광역·시군구)·분야(기본·주거·연금, 2단계 건강·의료·정책금융은 LATER_FIELDS로 대기)·주관 기관·공공 여부·이미 쓴 글과 같은 제도(duplicate_of) 판정을 추가. 공공기관이 아닌 것(광고)·현재 범위 밖 분야·이미 쓴 제도·뉴스 제목에 없는 이름/지역은 버림. 같은 종류 지역 제도는 "{종류} 지역별 비교" 후보 하나로 묶고 자동 등록 안 함(월요일 후보만). 종료된 제도 제외. 점수: 큰 광역 -0.3, 그 밖 광역 -0.6, 시군구 -0.9, 시군구는 관심도 5 미만 제외. 지역 제도는 주제 문장에 지역명 필수.
- 자동 등록(src/topic_service.py): 뉴스 주제도 자동 등록(비교·갱신 후보 제외), 작업 체크포인트 topic_origin에 출처 저장. '못 찾음' 알림 하루 한 번.
- 자동 발행 조건(src/news_gate.py, 새 파일): 공식 사이트 서로 다른 2곳 이상(뉴스·법령 자료 제외) + 못 찾은 숫자 0 + 새 분야는 그 분야 첫 3편 사람 확인 + 다른 회사 AI '실제 운영·지금 신청 가능' 확인(Codex gpt-6.2-sol high, 5시간 50% 넘거나 오류면 agy gemini-4-neon-high, 둘 다 실패면 불통과). 앞 조건에서 걸리면 AI 생략. 승인 요청 맨 위에 "[뉴스 주제: 자동 발행 가능]" 또는 "[뉴스 주제: 버튼 승인 필요] 이유".
- 판정은 그날(한국 날짜)만 유효: 아침 8시 자동 발행 때 차례가 온 뉴스 글만 최대 2편 다시 확인(확인 중엔 텔레그램 버튼 처리가 멈추므로 제한, AI 대기 300초). 확인 중 다시 만들기·취소로 글이 바뀌면 늦은 판정은 버림. 불통과 글은 daily_guard가 사실 확인 보류 글처럼 건너뜀.
- 발행된 뉴스 제도는 data/topic_seeds_news.txt에 자동 추가(지역별 비교 글 제외, 형식 미리 검사, 줄바꿈 문자 제거). 이 목록에서 다시 고른 주제도 뉴스 주제로 보고 같은 조건 적용(remembered). 지역 제도는 지역명을 태그로.
- 반영 안 한 리뷰 지적(이유): 기존 뉴스 글의 자동 갱신은 사용자가 이미 승인한 auto_refresh 기준이 따로 적용되는 경로라 범위 밖. 승인 대기 중 사람이 워드프레스에서 직접 고친 본문은 일반 글과 같은 기준(사람 책임)이라 판정 다시 안 함.
- 파일: src/topic_finder.py, src/topic_store.py, src/topic_service.py, src/news_gate.py, src/orchestrator.py, src/daily_guard.py, src/pipeline.py(extra_tags), scripts/jobctl.py, scripts/find_topics.py, tests(test_news_gate 신규, topic_finder·topic_auto). 커밋 bb202a5, 782ffce, c37406e, 938c21b.
- 검증: 전체 테스트 808개 통과. Codex·agy 실제 호출로 JSON 판정 받는 것 확인(각 20~30초). jalcheck-worker 재시작·사이트 200. 기존 대기 글은 topic_origin이 없어 영향 없음.
- 남은 일: 실제 뉴스 주제 글이 처음 나오면 승인 요청 표시·아침 재확인 로그 확인. 최적화 ④(Gemini 자동 캐시)·⑤(쉬운 일 가벼운 모델) 확인 후 진행. 2단계 분야(건강·의료·정책금융)는 1단계 운영 뒤 NEWS_FIELDS에 추가.

## 2026-11-24 최적화 ④(Gemini 자동 캐시) 확인 결과: 보류
- 지시: 사용자("1": ④ 먼저 확인) / 작업: 오라클2 Claude / 호출 구조 조사: Gemini neon-high
- 실험(gemini-3.8-flash, 우리 선불 키, 약 2.2만 토큰 같은 자료를 앞에 둔 요청 23회): 캐시 적중 2회뿐(첫 실험 3회 중 2회, 이후 20회 0회). 시스템 지시 같음/다름, JSON 설정 있음/없음 모두 적중 없음 → 자동 캐시는 "할인 보장 없음"(공식 문서)대로 거의 기대할 수 없음.
- 조사: 글 쓰기·검토·사실 확인·숫자 대조가 같은 자료를 공유하지만 시스템 지시와 자료 앞 문구가 달라 앞부분이 다름. 고치려면 4종 프롬프트 구조를 바꿔야 함(글 품질 위험).
- 판단: 적중이 불확실한 할인 때문에 프롬프트 구조를 흔들지 않음. 명시적 캐시(cachedContents)는 할인 확실하지만 보관 요금·캐시 수명 관리가 붙어 비용 대비 복잡. 비용이 문제가 되면 그때 다시 검토.

## 2026-11-24 최적화 ⑤(쉬운 일 가벼운 모델) 확인 결과: 하지 않음
- 지시: 사용자("1" + "글 품질이 항상 최우선, 효율 때문에 품질을 낮추면 안 됨") / 작업: 오라클2 Claude
- 후보였던 호출: 분류 고르기(choose_category, 약 600토큰, 글 1편에 1번), 주제 문장 다듬기(phrase, 하루 1~2번), 뉴스 제도 찾기(discover, 하루 2번).
- 판단: 셋을 다 합쳐도 하루 Gemini 입력의 몇 % 안 됨(큰 비용은 자료 원문을 보내는 글쓰기·검토·사실 확인). 반면 discover는 광고·중복·지역 판정, phrase는 글 방향을 정하는 주제 문장, 분류는 사이트 구조에 영향 → 품질에 닿는 판단이라 아끼는 양에 비해 위험이 큼. 그대로 1순위 모델 유지.
- 원칙(사용자): 비용·속도 최적화는 과거 글로 비교해 품질 손실이 없다는 근거가 있을 때만.

## 2026-11-24 예약 확인: 국가장학금 작업 12(임시 글 105) 다시 쓸 시점 — 공고 나옴, 사용자 선택 대기
- 교육부·한국장학재단 11-23 발표: 2027학년도 1학기 1차 신청 2026-11-24 09:00 ~ 2027-01-07 18:00(작년보다 마감 늦음). Ⅰ유형 연간 가 600만·나 440만·다 360만·라 100만, 마 제외, 기초·차상위 전액. 다자녀 첫째·둘째 가 610만 등. 지방국립대 전액 장학금도 국가장학금 신청으로만. 출처: 뉴스핌 20261123000075, 파이낸셜뉴스 202611231441037465, 동양일보 875249(공식 보도자료 원문은 신청 전 kosaf.go.kr·korea.kr에서 재확인 필요). redo는 하지 않음, 사용자 번호 선택 대기.

## 2026-11-24 텔레그램 [다음 글] 버튼 헷갈림 수정
- 지시: 사용자("다음 승인 대기 글 눌렀는데 왜 46도 뜨고 48도 떠?"). 작업·검토: Claude(작은 화면 동작 수정이라 별도 AI 리뷰 생략).
- 원인: 맨 아래 버튼(가장 오래 기다린 글=46)과 46번 안내에 붙은 버튼(46번 다음=48)이 이름이 같았고, 같은 11:19에 둘 다 처리됨 → 두 번 눌림(첫 응답으로 화면이 밀려 새 버튼이 손가락 밑에 옴)으로 판단. 순서 자체는 정상(오래된 순).
- 변경: 버튼 이름 구분("가장 오래 기다린 글부터 보기", "N번 다음 글 보기"), `handle_next`에 3초 연달아 누름 무시(`NEXT_DEBOUNCE_SECONDS`, 처리 끝난 시각 기준, 실패 시 바로 재시도 가능).
- 파일: src/telegram_notifier.py, src/orchestrator.py, tests/test_telegram.py, tests/test_orchestrator.py. 검증: pytest 809 통과, jalcheck-worker 재시작.

## 2026-11-24 한국 통로(해외 차단 기관 사이트 읽기) — 지시: 사용자, 작업: Claude(오라클2), 검토: Gemini neon-high
- 원인: myhome.go.kr·nhuf.molit.go.kr은 일본(오라클1·2)·Gemini 서버에서 접속 자체가 안 됨(해외 차단). 작업 51(신생아 특례대출) 실패.
- 구조: 사용자 집의 24시간 노트북(윈도우10, 멜론용, 자동 로그인)이 `ssh -N -R 127.0.0.1:18081 ubuntu@오라클1` → 오라클1 안쪽에 SOCKS 통로. 오라클2 systemd `korea-relay-forward`가 `-L 18081` 로 이어받음. `source_fetcher.fetch_sources`는 직접 접속 실패(ConnectFailedError) 시 `socks5://127.0.0.1:18081`로 재시도 → 그래도 안 되면 기존 Gemini 대신 읽기. 통로로만 열린 호스트는 6시간 동안 바로 통로로.
- 보안: 내부 주소 차단(SSRF)은 그대로(IP를 우리가 확인 후 IP로 연결). 오라클1 authorized_keys의 노트북 열쇠 = `restrict,port-forwarding,permitlisten="127.0.0.1:18081",permitopen="127.0.0.1:1",command="/bin/false"` (다른 포트·정방향·명령 실행 거절 시험 완료. `permitopen="none"`은 열쇠 자체가 무효가 되니 쓰지 말 것). 비밀 열쇠는 노트북에서 만들어 밖으로 안 나감. 백업 `~/.ssh/authorized_keys.bak-20261124b`(오라클1).
- 감수한 위험: 통로는 오라클1·2 안쪽 127.0.0.1에서 인증 없이 쓰임 → 두 서버가 해킹되면 집 공유기 안쪽으로 접근 가능. 노트북 OpenSSH ≥8.7이면 `PermitRemoteOpen=*:80 *:443`로 웹 포트만 허용(윈도우10 기본 8.1은 미지원).
- 노트북 쪽: `%USERPROFILE%\.korea-relay\`(key, known_hosts, run.ps1), 작업 스케줄러 "Korea Relay Tunnel"(로그인 시, 끊기면 30초 뒤 재접속). 다시 설치는 `scripts/korea_relay/setup_laptop.ps1`(__KNOWN_HOST__를 오라클1 ED25519 호스트 키 줄로 채워 배포).
- 추가 수정: 마이홈포털은 `<head>` 중복 + 본문이 `<main>` 밖 `#contentsArea`라 본문 0자로 판단되던 문제 → 추출기 수정(8d168d6).
- 커밋: b9d0839, 2bfdb66, e0623c0, 8d168d6. 테스트 810 통과. 실제 검증: 오라클2에서 relay_ready True, 51번 자료 두 페이지 통로로 읽힘(7,581자). 임시 배포 파일 relay-setup-k7q2.ps1 삭제(404 확인).
- 상태 확인: `.venv/bin/python -c "from src.source_fetcher import relay_ready;print(relay_ready())"` / `systemctl status korea-relay-forward`.
