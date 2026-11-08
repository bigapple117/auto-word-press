<?php
/**
 * Plugin Name: Jalcheck SEO (최소 SEO)
 * Description: 검색 결과 설명문(meta description), 공유 미리보기(Open Graph·Twitter), 구조화 데이터(JSON-LD)를 출력한다.
 *              Yoast·Rank Math 같은 SEO 플러그인이 켜져 있으면 아무것도 하지 않는다(중복 방지).
 * 원본: auto-word-press/deploy/wp/mu-plugins/jalcheck-seo.php (서버의 wp-content/mu-plugins/에 복사해 사용)
 */

if (!defined('ABSPATH')) {
    exit;
}

function jalcheck_seo_disabled(): bool {
    $other = defined('WPSEO_VERSION') || defined('RANK_MATH_VERSION') || defined('AIOSEO_VERSION')
        || defined('SEOPRESS_VERSION');
    // add_filter('jalcheck_seo_enabled', '__return_false'); 로 직접 끌 수 있다
    return $other || !apply_filters('jalcheck_seo_enabled', true);
}

/** HTML 태그·줄바꿈을 뺀 짧은 설명문 */
function jalcheck_seo_clean(string $text, int $max = 160): string {
    $text = trim(preg_replace('/\s+/u', ' ', wp_strip_all_tags($text)));
    if (mb_strlen($text) > $max) {
        $text = rtrim(mb_substr($text, 0, $max - 1)) . '…';
    }
    return $text;
}

function jalcheck_seo_meta(string $attr, string $key, string $value): void {
    if ($value === '') {
        return;
    }
    printf("<meta %s=\"%s\" content=\"%s\" />\n", $attr, esc_attr($key), esc_attr($value));
}

function jalcheck_seo_json_ld(array $data): void {
    // 기본 이스케이프(슬래시 포함)를 유지해 </script>로 태그가 끊기지 않게 한다
    echo '<script type="application/ld+json">' . wp_json_encode($data, JSON_UNESCAPED_UNICODE) . "</script>\n";
}

add_action('wp_head', function () {
    if (jalcheck_seo_disabled()) {
        return;
    }
    $site_name = get_bloginfo('name');
    $home = home_url('/');
    $publisher = ['@type' => 'Organization', 'name' => $site_name, 'url' => $home];
    $icon = get_site_icon_url(512);
    if ($icon) {
        $publisher['logo'] = ['@type' => 'ImageObject', 'url' => $icon];
    }

    if (is_singular('post')) {
        $post = get_queried_object();
        // 공개된 글에서만(비공개·임시 글 미리보기·비밀번호 글 제외)
        if (!($post instanceof WP_Post) || is_preview() || !is_post_publicly_viewable($post)
            || $post->post_password !== '') {
            return;
        }
        $title = wp_strip_all_tags(get_the_title($post));
        $desc = jalcheck_seo_clean($post->post_excerpt !== '' ? $post->post_excerpt : $post->post_content);
        $url = get_permalink($post);
        $image = has_post_thumbnail($post) ? (string) get_the_post_thumbnail_url($post, 'full') : '';
        $author = get_the_author_meta('display_name', (int) $post->post_author);

        jalcheck_seo_meta('name', 'description', $desc);
        jalcheck_seo_meta('property', 'og:type', 'article');
        jalcheck_seo_meta('property', 'og:site_name', $site_name);
        jalcheck_seo_meta('property', 'og:locale', 'ko_KR');
        jalcheck_seo_meta('property', 'og:title', $title);
        jalcheck_seo_meta('property', 'og:description', $desc);
        jalcheck_seo_meta('property', 'og:url', $url);
        jalcheck_seo_meta('property', 'og:image', $image);
        jalcheck_seo_meta('property', 'article:published_time', get_the_date('c', $post));
        jalcheck_seo_meta('property', 'article:modified_time', get_the_modified_date('c', $post));
        jalcheck_seo_meta('name', 'twitter:card', $image ? 'summary_large_image' : 'summary');
        jalcheck_seo_meta('name', 'twitter:title', $title);
        jalcheck_seo_meta('name', 'twitter:description', $desc);
        jalcheck_seo_meta('name', 'twitter:image', $image);

        $article = [
            '@context' => 'https://schema.org',
            '@type' => 'Article',
            'headline' => mb_substr($title, 0, 110),
            'description' => $desc,
            'datePublished' => get_the_date('c', $post),
            'dateModified' => get_the_modified_date('c', $post),
            'mainEntityOfPage' => ['@type' => 'WebPage', '@id' => $url],
            // 이 사이트의 작성 계정은 '잘체크 편집팀'(조직 명의)이다. 개인 작성자를 두면 Person으로 바꿀 것
            'author' => ['@type' => 'Organization', 'name' => $author ?: $site_name,
                         'url' => get_author_posts_url((int) $post->post_author)],
            'publisher' => $publisher,
            'inLanguage' => 'ko-KR',
        ];
        if ($image) {
            $article['image'] = [$image];
        }
        jalcheck_seo_json_ld($article);
        return;
    }

    if (is_front_page() && !is_paged()) {
        $desc = jalcheck_seo_clean(get_bloginfo('description'));
        jalcheck_seo_meta('name', 'description', $desc);
        jalcheck_seo_meta('property', 'og:type', 'website');
        jalcheck_seo_meta('property', 'og:site_name', $site_name);
        jalcheck_seo_meta('property', 'og:locale', 'ko_KR');
        jalcheck_seo_meta('property', 'og:title', $site_name);
        jalcheck_seo_meta('property', 'og:description', $desc);
        jalcheck_seo_meta('property', 'og:url', $home);
        jalcheck_seo_json_ld([
            '@context' => 'https://schema.org',
            '@type' => 'WebSite',
            'name' => $site_name,
            'url' => $home,
            'description' => $desc,
            'inLanguage' => 'ko-KR',
            'publisher' => $publisher,
        ]);
        return;
    }

    if (is_category()) {
        jalcheck_seo_meta('name', 'description', jalcheck_seo_clean(category_description()));
    }
}, 5);

// 태그·글쓴이 목록 페이지는 검색에서 뺀다. 태그 대부분이 글 1개뿐이라 내용 없는 페이지로 보이고,
// 글쓴이 페이지는 편집팀 하나라 홈 목록과 같다. 링크는 계속 따라가게(follow) 두어 글은 정상 수집된다.
add_filter('wp_robots', function (array $robots): array {
    if (!jalcheck_seo_disabled() && (is_tag() || is_author())) {
        $robots['noindex'] = true;
        $robots['follow'] = true;
    }
    return $robots;
});

// 사이트 지도에서도 같은 페이지를 뺀다(검색에서 뺀 주소를 사이트 지도로 알리면 서치 콘솔 경고가 생김)
add_filter('wp_sitemaps_taxonomies', function (array $taxonomies): array {
    if (!jalcheck_seo_disabled()) {
        unset($taxonomies['post_tag']);
    }
    return $taxonomies;
});
add_filter('wp_sitemaps_add_provider', function ($provider, string $name) {
    return (!jalcheck_seo_disabled() && $name === 'users') ? false : $provider;
}, 10, 2);

// 구글 서치 콘솔·네이버 서치어드바이저 소유 확인(HTML 태그 방식). 둘 다 홈 화면에서 이 줄을 확인하며, 지우면 소유 확인이 풀린다.
// 다른 SEO 플러그인을 켜도 확인이 끊기지 않도록 위의 끄기 설정과 상관없이 출력한다.
add_action('wp_head', function () {
    if (is_front_page()) {
        jalcheck_seo_meta('name', 'google-site-verification', 'Z-S54PxLpagUsG11FxGv402jW7IsE80oCdyoBdCIeW4');
        jalcheck_seo_meta('name', 'naver-site-verification', '7c12fe59e9bf145512e212d961b84638b99459bc');
    }
}, 1);

// 옛 분류 주소를 새 주소로 영구 이동(301). 2026-11-08 '지원금·세금'(support-tax)을 '지원금·복지'(benefits)로 바꾸며 추가.
// 이미 검색·공유된 옛 주소가 404가 되지 않게 한다. 분류 주소를 또 바꾸면 여기에 한 줄씩 더한다.
add_action('template_redirect', function () {
    if (!is_404()) {
        return;
    }
    $moved = ['support-tax' => 'benefits'];
    $path = (string) wp_parse_url($_SERVER['REQUEST_URI'] ?? '', PHP_URL_PATH);
    if (preg_match('#^/category/([^/]+)(/.*)?$#', $path, $m) && isset($moved[$m[1]])) {
        wp_safe_redirect(home_url('/category/' . $moved[$m[1]] . ($m[2] ?? '/')), 301);
        exit;
    }
});
