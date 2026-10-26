# Auto WordPress Publisher (구글 시트 연동 워드프레스 자동 발행 시스템)

## 프로젝트 목적
구글 스프레드시트에 주제/키워드를 입력하면 LLM(OpenAI/Gemini)을 통해 SEO 최적화된 제목 및 반응형 HTML 본문 코드를 자동 생성하고, 워드프레스(WordPress) REST API를 통해 자동으로 게시글을 발행(초안/즉시발행)하여 포스트 URL과 작업일시를 시트에 피드백하는 엔드투엔드 자동화 솔루션.

## 시스템 아키텍처 옵션
1. **Google Apps Script (GAS) 단독 서버리스 모드**:
   - 구글 스프레드시트의 Apps Script 편집기에 코드를 적용하여 별도 서버 없이 시트 상단 커스텀 메뉴(`🤖 자동발행 봇`) 클릭만으로 동작.
2. **Python 워커 백엔드 모드 (OCI 서버 연동)**:
   - OCI VM에서 동작하며, Google Sheets API(gspread) + LLM API + WordPress REST API를 연동하여 대량 처리, 크론 자동화, 썸네일 자동 생성 및 재시도 큐 관리.

## 주요 구성 요소
- 구글 스프레드시트 템플릿 (주제, 상태, 제목, 본문, URL, 일시)
- 프롬프트 엔지니어링 (의료/블로그/정보성 글 전문 HTML 스타일 래퍼 템플릿)
- 워드프레스 REST API 클라이언트 (Application Password 인증)
- 안전한 재시도 및 중복 발행 방지 메커니즘
