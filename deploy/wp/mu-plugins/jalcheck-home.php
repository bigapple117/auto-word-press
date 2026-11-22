<?php
/**
 * Plugin Name: Jalcheck Home (홈 글 목록 페이지 나누기)
 * Description: 홈 화면 카드 목록을 /page/2/ 같은 주소로 넘겨 볼 수 있게 한다.
 *              1쪽은 맨 위 큰 글 1개 + 카드 9개, 2쪽부터는 큰 글 없이 그다음 글부터 카드 9개씩.
 *              (검색엔진이 링크만 따라가도 옛 글까지 닿고, 쪽마다 내용이 달라 중복 주소가 생기지 않게)
 * 원본: auto-word-press/deploy/wp/mu-plugins/jalcheck-home.php (서버의 wp-content/mu-plugins/에 복사해 사용)
 * 짝: deploy/wp/templates/home.html 의 카드 목록(jc-card-query)은 이 주 목록을 그대로 쓴다(inherit).
 *     이 파일을 지우면 홈 카드 목록이 큰 글과 1개 겹치므로 home.html도 함께 되돌린다.
 */

if (!defined('ABSPATH')) {
    exit;
}

const JALCHECK_HOME_FEATURE = 1;   // 1쪽 맨 위 큰 글 수(home.html의 jc-feature-query)
const JALCHECK_HOME_PER_PAGE = 9;  // 카드 수(3칸 격자라 3의 배수)

function jalcheck_home_is_list(WP_Query $q): bool
{
    return !is_admin() && $q->is_main_query() && $q->is_home() && !$q->is_feed();
}

// 큰 글로 보여 준 최신 글을 건너뛰고, 쪽마다 9개씩 이어서 보여 준다.
add_action('pre_get_posts', static function (WP_Query $q): void {
    if (!jalcheck_home_is_list($q)) {
        return;
    }
    $page = max(1, (int) $q->get('paged'));
    $q->set('posts_per_page', JALCHECK_HOME_PER_PAGE);
    $q->set('offset', JALCHECK_HOME_FEATURE + ($page - 1) * JALCHECK_HOME_PER_PAGE);
    $q->set('ignore_sticky_posts', true);
});

// offset을 쓰면 전체 쪽수 계산에 큰 글이 섞이므로 그만큼 뺀다.
add_filter('found_posts', static function ($found, WP_Query $q) {
    if (jalcheck_home_is_list($q)) {
        return max(0, (int) $found - JALCHECK_HOME_FEATURE);
    }
    return $found;
}, 10, 2);

// 2쪽부터는 맨 위 큰 글 칸(새로 확인한 제도)을 뺀다. 1쪽과 같은 글이 반복되지 않게.
// 그리기 전에 끊어(pre_render_block) 큰 글 조회 자체를 하지 않는다.
add_filter('pre_render_block', static function ($pre, array $block) {
    if (is_home() && is_paged() && ($block['blockName'] ?? '') === 'core/group'
        && str_contains((string) ($block['attrs']['className'] ?? ''), 'jc-home-sec--feature')) {
        return '';
    }
    return $pre;
}, 10, 2);
