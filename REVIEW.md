# REVIEW

## 2026-11-06 바로가기 링크: 정부 자료 2곳이 안내한 사업 누리집 첫 화면 인정(link_check)
- 작성자: Claude / 검토자: Codex (codex exec, read-only, 변경 내용 stdin 전달)
- 참고: `codex-review.py --uncommitted`는 샌드박스 오류(bwrap: loopback: Failed RTM_NEWADDR)로 2회 BLOCKED → 붙여 넣기 방식으로 재요청
- 1차: CHANGES_REQUESTED — (1) 공식 자료 한 곳 본문 등장만으로 인정(광고·주의 예시 주소 통과 위험) (2) 리다이렉트로 외부 본문을 읽은 경우 (3) 단축 주소 (4) 조건별 분리 테스트 누락
- 2차: CHANGES_REQUESTED — (1) 공식 2곳에 같은 단축 주소가 있으면 통과 (2) 리다이렉트 테스트가 fetcher 실제 동작을 검증하지 않음
- 3차: **PASS** — 서로 다른 공식 사이트 2곳 이상에 도메인만 적힌 경우 첫 화면만 인정, 깊은·단축 주소 불가, 실제 fetch_source 가짜 네트워크 리다이렉트 테스트 (근거 src/link_check.py:93–112, 129, tests/test_content.py:434, source_fetcher.py:357·371)
- 검증: Codex 정적 검토. Claude 쪽 pytest 274 통과
- 원문: data/design-review/codex-officiallink-*.txt

## 2026-11-05 jalcheck-reading.php(목차·함께 보면 좋은 제도) + jc-article.css 추가 규칙
- 작성자: Claude / 검토자: Codex (codex exec, read-only, 파일 내용 stdin 전달)
- 참고: `codex-review.py --uncommitted`는 샌드박스 오류(bwrap: loopback: Failed RTM_NEWADDR)로 BLOCKED → 같은 범위를 내용 붙여 넣기 방식으로 재요청
- 1차: CHANGES_REQUESTED — (1) 발췌문 생성 중 처리 소모 (2) 기존 ID와 생성 ID 충돌 (3) 태그·카테고리만 바뀔 때 캐시 무효화 누락 (4) .jc-article 밖 h2 처리
- 2차: CHANGES_REQUESTED — (1) 앞부분·엔티티 ID 충돌 (2) deleted_term_relationships 누락 (3) .jc-article 뒤 h2 처리
- 3차: **PASS** — 3개 모두 해결, 새 운영 영향 문제 없음 (근거 jalcheck-reading.php:54–66, 57–62, 81–86, 117, 178–182)
- 검증: 정적 검토만(Codex). Claude 쪽 실제 검증: php -l 통과, 실제 글 본문으로 목차 생성 시험(6개 항목, 참고 자료 제외), ID 충돌·바깥 h2·짧은 글(소제목 2개) 시험 통과
- 원문: data/design-review/codex-reading*.txt
