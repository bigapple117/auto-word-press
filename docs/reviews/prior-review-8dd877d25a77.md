# REVIEW

## 2026-11-05 jalcheck-reading.php(목차·함께 보면 좋은 제도) + jc-article.css 추가 규칙
- 작성자: Claude / 검토자: Codex (codex exec, read-only, 파일 내용 stdin 전달)
- 참고: `codex-review.py --uncommitted`는 샌드박스 오류(bwrap: loopback: Failed RTM_NEWADDR)로 BLOCKED → 같은 범위를 내용 붙여 넣기 방식으로 재요청
- 1차: CHANGES_REQUESTED — (1) 발췌문 생성 중 처리 소모 (2) 기존 ID와 생성 ID 충돌 (3) 태그·카테고리만 바뀔 때 캐시 무효화 누락 (4) .jc-article 밖 h2 처리
- 2차: CHANGES_REQUESTED — (1) 앞부분·엔티티 ID 충돌 (2) deleted_term_relationships 누락 (3) .jc-article 뒤 h2 처리
- 3차: **PASS** — 3개 모두 해결, 새 운영 영향 문제 없음 (근거 jalcheck-reading.php:54–66, 57–62, 81–86, 117, 178–182)
- 검증: 정적 검토만(Codex). Claude 쪽 실제 검증: php -l 통과, 실제 글 본문으로 목차 생성 시험(6개 항목, 참고 자료 제외), ID 충돌·바깥 h2·짧은 글(소제목 2개) 시험 통과
- 원문: data/design-review/codex-reading*.txt
