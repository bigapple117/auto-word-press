<?php
/**
 * Plugin Name: Jalcheck Theme (글꼴·댓글)
 * Description: 사이트 글꼴(IBM Plex Sans KR, SIL OFL)을 구글 폰트에서 불러오고, 댓글·핑백을 모든 글에서 닫는다.
 *              글 데이터(댓글 설정값)는 바꾸지 않고 화면·접수만 막는다. 이 파일을 지우면 원래대로 돌아간다.
 * 원본: auto-word-press/deploy/wp/mu-plugins/jalcheck-theme.php (서버의 wp-content/mu-plugins/에 복사해 사용)
 * 글꼴 적용 규칙(font-family)은 deploy/wp/jc-site.css에 있다.
 */

if (!defined('ABSPATH')) {
    exit;
}

const JALCHECK_FONT_URL = 'https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap';

add_action('wp_enqueue_scripts', static function (): void {
    if (!apply_filters('jalcheck_theme_font', true)) {
        return;
    }
    wp_enqueue_style('jalcheck-font', JALCHECK_FONT_URL, [], null);
});

/** 글꼴 서버에 미리 연결해 첫 화면 지연을 줄인다 */
add_filter('wp_resource_hints', static function (array $urls, string $type): array {
    if ($type === 'preconnect' && apply_filters('jalcheck_theme_font', true)) {
        $urls[] = 'https://fonts.googleapis.com';
        $urls[] = ['href' => 'https://fonts.gstatic.com', 'crossorigin' => 'anonymous'];
    }
    return $urls;
}, 10, 2);

/** 댓글·핑백 닫기: 입력란이 안 보이고, 직접 보내도 접수되지 않는다 */
add_filter('comments_open', '__return_false', 20);
add_filter('pings_open', '__return_false', 20);
add_filter('comments_array', static function ($comments) {
    return is_admin() ? $comments : [];
}, 20);
