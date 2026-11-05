<?php
/**
 * Plugin Name: Jalcheck Reading (목차·함께 보면 좋은 제도)
 * Description: 잘체크 자동 글(.jc-article)에 접고 펴는 목차와 글 끝 "함께 보면 좋은 제도" 목록을 화면에 보여 줄 때만 덧붙인다.
 *              글 원본(DB)은 바꾸지 않는다. 이 파일을 지우면 바로 원래대로 돌아간다.
 * 원본: auto-word-press/deploy/wp/mu-plugins/jalcheck-reading.php (서버의 wp-content/mu-plugins/에 복사해 사용)
 */

if (!defined('ABSPATH')) {
    exit;
}

const JALCHECK_READING_MIN_TOC = 3;      // 소제목이 이보다 적으면 목차를 붙이지 않는다
const JALCHECK_READING_RELATED = 3;      // 함께 보면 좋은 제도 개수
const JALCHECK_READING_SKIP = ['참고 자료']; // 목차에서 뺄 소제목

/** 지금 보여 주는 것이 공개 글 화면의 본문인지 */
function jalcheck_reading_applies(string $content): bool {
    if (is_admin() || is_feed() || wp_doing_ajax() || (defined('REST_REQUEST') && REST_REQUEST)) {
        return false;
    }
    if (!is_singular('post') || !is_main_query()) {
        return false;
    }
    // 화면 아래 다른 글 목록(쿼리 블록) 안의 본문에는 붙이지 않는다
    if ((int) get_the_ID() !== (int) get_queried_object_id()) {
        return false;
    }
    return strpos($content, 'jc-article') !== false;
}

/** $start 위치의 <div>가 닫히는 곳(닫는 태그 바로 뒤). 짝을 못 찾으면 본문 끝 */
function jalcheck_reading_div_end(string $html, int $start): int {
    if (!preg_match_all('#<(/?)div\b[^>]*>#i', $html, $m, PREG_OFFSET_CAPTURE, $start)) {
        return strlen($html);
    }
    $depth = 0;
    foreach ($m[0] as $i => $tag) {
        $depth += $m[1][$i][0] === '/' ? -1 : 1;
        if ($depth === 0) {
            return $tag[1] + strlen($tag[0]);
        }
    }
    return strlen($html);
}

/** 소제목에 이동용 id를 달고 목차 상자를 만든다 */
function jalcheck_reading_add_toc(string $content): string {
    // .jc-article 앞에 따로 넣은 내용은 건드리지 않는다
    $start = strpos($content, 'class="jc-article');
    if ($start === false) {
        return $content;
    }
    $start = (int) strrpos(substr($content, 0, $start), '<');
    $end = jalcheck_reading_div_end($content, $start);

    // 이미 쓰인 id는 문서 전체에서 모은다(엔티티는 풀어서 비교)
    $used = [];
    if (preg_match_all('/\sid\s*=\s*(["\'])(.*?)\1/i', $content, $all)) {
        foreach ($all[2] as $raw) {
            $used[html_entity_decode($raw, ENT_QUOTES, 'UTF-8')] = true;
        }
    }
    $head = substr($content, 0, $start);
    $tail = substr($content, $end);
    $content = substr($content, $start, $end - $start);
    $items = [];
    $n = 0;
    $body = $content;
    $content = preg_replace_callback(
        '#<h2(\s[^>]*)?>(.*?)</h2>#is',
        static function (array $m) use (&$items, &$n, &$used): string {
            $attrs = $m[1] ?? '';
            $text = trim(html_entity_decode(wp_strip_all_tags($m[2]), ENT_QUOTES, 'UTF-8'));
            if ($text === '' || in_array($text, JALCHECK_READING_SKIP, true)) {
                return $m[0];
            }
            if (preg_match('/\sid\s*=\s*(["\'])(.*?)\1/i', $attrs, $idm)) {
                $id = html_entity_decode($idm[2], ENT_QUOTES, 'UTF-8');
            } else {
                do {
                    $n++;
                    $id = 'jc-sec-' . $n;
                } while (isset($used[$id]));
                $used[$id] = true;
                $attrs .= ' id="' . esc_attr($id) . '"';
            }
            $items[] = ['id' => $id, 'text' => $text];
            return '<h2' . $attrs . '>' . $m[2] . '</h2>';
        },
        $content
    );
    if (!is_string($content)) {   // 정규식 처리 실패: 원래 본문 그대로
        return $head . $body . $tail;
    }
    if (count($items) < JALCHECK_READING_MIN_TOC) {
        return $head . $content . $tail;
    }

    $html = '<details class="jc-toc"><summary>이 글의 순서 (' . count($items) . '개)</summary><ol>';
    foreach ($items as $it) {
        $html .= '<li><a href="#' . esc_attr($it['id']) . '">' . esc_html($it['text']) . '</a></li>';
    }
    $html .= '</ol></details>';

    // 넣을 자리: 신청·조회 바로가기 다음 → 기준 시점 한 줄 다음 → 첫 소제목 앞
    if (preg_match('#<nav class="jc-links"[^>]*>.*?</nav>#s', $content, $mm, PREG_OFFSET_CAPTURE)) {
        $pos = $mm[0][1] + strlen($mm[0][0]);
    } elseif (preg_match('#<p class="jc-reference"[^>]*>.*?</p>#s', $content, $mm, PREG_OFFSET_CAPTURE)) {
        $pos = $mm[0][1] + strlen($mm[0][0]);
    } else {
        $pos = stripos($content, '<h2');
        if ($pos === false) {
            return $head . $content . $tail;
        }
    }
    return $head . substr($content, 0, $pos) . "\n" . $html . "\n" . substr($content, $pos) . $tail;
}

/** 관련 글 번호: 같은 태그 → 같은 카테고리 → 최신 글 순서로 채운다 (12시간 저장) */
function jalcheck_reading_related_ids(int $post_id): array {
    $ver = (int) get_option('jalcheck_reading_ver', 1);
    $key = 'jc_related_' . $post_id . '_' . $ver;
    $cached = get_transient($key);
    if (is_array($cached)) {
        return array_map('intval', $cached);
    }

    $ids = [];
    $base = [
        'post_type' => 'post',
        'post_status' => 'publish',
        'ignore_sticky_posts' => true,
        'no_found_rows' => true,
        'fields' => 'ids',
        'orderby' => 'date',
        'order' => 'DESC',
    ];
    $groups = [];
    $tags = wp_get_post_tags($post_id, ['fields' => 'ids']);
    if ($tags) {
        $groups[] = ['tag__in' => array_map('intval', $tags)];
    }
    $cats = wp_get_post_categories($post_id);
    if ($cats) {
        $groups[] = ['category__in' => array_map('intval', $cats)];
    }
    $groups[] = [];
    foreach ($groups as $extra) {
        $need = JALCHECK_READING_RELATED - count($ids);
        if ($need <= 0) {
            break;
        }
        $q = new WP_Query($base + $extra + [
            'posts_per_page' => $need,
            'post__not_in' => array_merge([$post_id], $ids),
        ]);
        $ids = array_merge($ids, array_map('intval', $q->posts));
    }
    set_transient($key, $ids, 12 * HOUR_IN_SECONDS);
    return $ids;
}

/** 글이 새로 공개·수정·삭제되면 저장해 둔 관련 글 목록을 모두 새로 만들게 한다 */
function jalcheck_reading_bump(): void {
    update_option('jalcheck_reading_ver', (int) get_option('jalcheck_reading_ver', 1) + 1, false);
}
add_action('transition_post_status', static function ($new, $old, $post): void {
    if ($post instanceof WP_Post && $post->post_type === 'post' && ($new === 'publish' || $old === 'publish')) {
        jalcheck_reading_bump();
    }
}, 10, 3);
add_action('set_object_terms', static function ($object_id, $terms, $tt_ids, $taxonomy): void {
    if (in_array($taxonomy, ['post_tag', 'category'], true) && get_post_type((int) $object_id) === 'post') {
        jalcheck_reading_bump();
    }
}, 10, 4);
add_action('deleted_term_relationships', static function ($object_id, $tt_ids, $taxonomy): void {
    if (in_array($taxonomy, ['post_tag', 'category'], true) && get_post_type((int) $object_id) === 'post') {
        jalcheck_reading_bump();
    }
}, 10, 3);
add_action('delete_term', static function ($term, $tt_id, $taxonomy): void {
    if (in_array($taxonomy, ['post_tag', 'category'], true)) {
        jalcheck_reading_bump();
    }
}, 10, 3);

function jalcheck_reading_related_html(int $post_id): string {
    $ids = jalcheck_reading_related_ids($post_id);
    if (!$ids) {
        return '';
    }
    $html = '<aside class="jc-article jc-related" aria-label="함께 보면 좋은 제도"><p class="jc-related__title">함께 보면 좋은 제도</p><ul>';
    foreach ($ids as $id) {
        if (get_post_status($id) !== 'publish') {
            continue;
        }
        $cats = get_the_category($id);
        $cat = $cats ? $cats[0]->name : '';
        $thumb = get_the_post_thumbnail($id, 'thumbnail', ['class' => 'jc-related__img', 'alt' => '', 'loading' => 'lazy']);
        if ($thumb === '') {
            $thumb = '<span class="jc-related__img jc-related__img--empty" aria-hidden="true">' . esc_html($cat !== '' ? $cat : '잘체크') . '</span>';
        }
        $meta = trim($cat . ' · ' . get_the_date('Y.m.d', $id), ' ·');
        $html .= '<li><a class="jc-related__link" href="' . esc_url(get_permalink($id)) . '">' . $thumb
            . '<span class="jc-related__body"><span class="jc-related__name">' . esc_html(get_the_title($id)) . '</span>'
            . '<span class="jc-related__meta">' . esc_html($meta) . '</span></span></a></li>';
    }
    $html .= '</ul></aside>';
    return strpos($html, '<li>') === false ? '' : $html;
}

add_filter('the_content', static function ($content) {
    static $done = false;
    if (!is_string($content) || $done || doing_filter('get_the_excerpt') || doing_filter('wp_trim_excerpt')) {
        return $content;
    }
    if (strpos($content, 'class="jc-toc"') !== false || strpos($content, 'jc-related') !== false
        || !jalcheck_reading_applies($content)) {
        return $content;
    }
    if (!apply_filters('jalcheck_reading_enabled', true)) {
        return $content;
    }
    $done = true;
    $content = jalcheck_reading_add_toc($content);
    return $content . "\n" . jalcheck_reading_related_html((int) get_queried_object_id());
}, 20);
