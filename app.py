"""
田舎主義ステップメール生成システム（デモ版）
- CSV一括アップロード対応（Shift-JIS）
- 送信グループ管理
- 関連商品自動提案
- 複数トーン選択対応
- HTMLプレビュー機能
- 商品ページスクレイピング＆差し込み機能（FutureShop対応）
"""

import streamlit as st
import streamlit.components.v1 as components
import socket
import json
import sys
import webbrowser
import urllib.parse
import db_manager
import os
import subprocess
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from customer_manager import CustomerManager
from product_manager import ProductManager

# 定数
LRS_HOST = "localhost"
LRS_PORT = 5000


# =====================================================================
# スクレイピング可否チェック機能
# =====================================================================

# 利用規約でスクレイピングを明示的に禁止している主要サービス
_BLOCKED_DOMAINS: dict[str, str] = {
    "rakuten.co.jp":              "楽天市場（利用規約でスクレイピング禁止）",
    "rakuten.com":                "Rakuten（利用規約でスクレイピング禁止）",
    "amazon.co.jp":               "Amazon.co.jp（利用規約でスクレイピング禁止）",
    "amazon.com":                 "Amazon.com（利用規約でスクレイピング禁止）",
    "shopping.yahoo.co.jp":       "Yahoo!ショッピング（利用規約でスクレイピング禁止）",
    "store.shopping.yahoo.co.jp": "Yahoo!ショッピング（利用規約でスクレイピング禁止）",
    "auctions.yahoo.co.jp":       "ヤフオク！（利用規約でスクレイピング禁止）",
    "paypaymall.yahoo.co.jp":     "PayPayモール（利用規約でスクレイピング禁止）",
    "mercari.com":                "メルカリ（利用規約でスクレイピング禁止）",
    "wowma.jp":                   "auPAYマーケット（利用規約でスクレイピング禁止）",
    "qoo10.jp":                   "Qoo10（利用規約でスクレイピング禁止）",
    "zozo.jp":                    "ZOZOTOWN（利用規約でスクレイピング禁止）",
}


def _extract_domain(url: str) -> str:
    """URLからドメインを抽出（www.プレフィックスを除去）"""
    try:
        domain = urllib.parse.urlparse(url).netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain
    except Exception:
        return ""


def _check_blocked_domain(url: str) -> tuple[bool, str]:
    """ハードコードブロックリストに該当するか確認（サブドメインも対応）"""
    domain = _extract_domain(url)
    for blocked, reason in _BLOCKED_DOMAINS.items():
        if domain == blocked or domain.endswith("." + blocked):
            return True, reason
    return False, ""


def _check_robots_txt(url: str) -> tuple[str, str]:
    """
    robots.txtを取得・解析してスクレイピング可否を判定する。

    Returns:
        ("ok" | "blocked" | "warning", メッセージ)
    """
    try:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        target_path = parsed.path or "/"

        res = requests.get(
            robots_url,
            headers={"User-Agent": "python-requests/2.x"},
            timeout=10,
        )

        if res.status_code == 404:
            return "warning", f"robots.txt が見つかりませんでした（404）: {robots_url}"
        if res.status_code != 200:
            return "warning", f"robots.txt を取得できませんでした（HTTP {res.status_code}）"

        res.encoding = res.apparent_encoding or "utf-8"

        # User-agent: * / python-requests / python に対するルールを収集
        target_agents = {"*", "python-requests", "python"}
        current_agents: list[str] = []
        disallow_rules: list[str] = []
        allow_rules: list[str] = []

        for line in res.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition(":")
            key, val = key.strip().lower(), val.strip()
            if key == "user-agent":
                current_agents = [val.lower()]
            elif key == "disallow" and val and any(a in target_agents for a in current_agents):
                disallow_rules.append(val)
            elif key == "allow" and val and any(a in target_agents for a in current_agents):
                allow_rules.append(val)

        def _matches(rule: str, path: str) -> bool:
            return path.startswith(rule.rstrip("*"))

        is_disallowed = any(_matches(r, target_path) for r in disallow_rules)
        is_allowed    = any(_matches(r, target_path) for r in allow_rules)

        if is_disallowed and not is_allowed:
            matched = next(r for r in disallow_rules if _matches(r, target_path))
            return "blocked", (
                f"robots.txt によりこのパスへのアクセスは禁止されています。\n"
                f"該当ルール: Disallow: {matched}\n"
                f"robots.txt: {robots_url}"
            )

        return "ok", f"robots.txt に問題ありません（{robots_url}）"

    except requests.exceptions.Timeout:
        return "warning", "robots.txt の取得がタイムアウトしました（10秒）"
    except requests.exceptions.ConnectionError:
        return "warning", "robots.txt の取得中に接続エラーが発生しました"
    except Exception as e:
        return "warning", f"robots.txt チェック中にエラーが発生しました: {e}"


def check_scraping_permission(url: str) -> tuple[str, str]:
    """
    スクレイピング可否を総合判定する。
      1. ハードコードブロックリスト → "blocked"（即時）
      2. robots.txt              → "blocked" / "warning" / "ok"

    Returns:
        ("ok" | "blocked" | "warning", メッセージ)
    """
    is_blocked, reason = _check_blocked_domain(url)
    if is_blocked:
        return "blocked", f"このサービスはスクレイピングが利用規約で禁止されています。\n対象: {reason}"
    return _check_robots_txt(url)


# =====================================================================
# 商品スクレイピング機能
# =====================================================================

# --- 対応ECシステム別セレクター定義 ---
# 各エントリは (商品ブロックsel, 商品名sel, キャッチコピーsel, 価格sel, 画像sel, 商品URLsel) のタプル
# 画像はdata-lazy → data-src → src の順で試みる
_EC_PROFILES = [
    # ── FutureShop（フューチャーショップ）──
    # 対応サイト例: nishidaya.com / eirakuya.co.jp / ito-noen.com / itohkyuemon.co.jp
    # ※ itohkyuemon はトップページ(/)ではなくカテゴリ一覧ページを指定すること
    #   例: https://www.itohkyuemon.co.jp/c/category/ujicha
    # メイン画像(src)はブラウザ保存時にローカルパスになるため、
    # モーダルカルーセルの最初の img[data-lazy] から本番URLを取得する
    {
        "label": "FutureShop",
        "item":    "article.fs-c-productListItem",
        "name":    ".fs-c-productName__name",
        "copy":    ".fs-c-productName__copy",
        "price":   ".fs-c-productPrice__main__price",
        "image":   ".fs-c-productImageModalCarousel__figure__image",
        "url":     ".fs-c-productListItem__image a",
    },
    # ── カラーミーショップ ──
    {
        "label": "ColorMeShop",
        "item":    "ul.item_list > li, ul.goods_list > li",
        "name":    ".item_name, .goods_name",
        "copy":    None,
        "price":   ".item_price, .goods_price",
        "image":   "img",
        "url":     "a",
    },
    # ── BASE ──
    {
        "label": "BASE",
        "item":    ".item-list__item, .ItemList__item",
        "name":    ".item-list__item__name, .ItemName",
        "copy":    None,
        "price":   ".item-list__item__price, .ItemPrice",
        "image":   "img",
        "url":     "a",
    },
    # ── makeshop ──
    {
        "label": "makeshop",
        "item":    ".ms-item-box",
        "name":    ".ms-item-name",
        "copy":    None,
        "price":   ".ms-item-price",
        "image":   "img",
        "url":     "a",
    },
    # ── kubara（kubara.jp）──
    # Knockout.jsによる動的レンダリング → Playwrightで取得する
    # ブラウザレンダリング後のセレクター
    {
        "label": "kubara",
        "item":  "div.l_box.mdl_box_product_wide",
        "name":  "p.txt_product_name a",
        "copy":  "p.txt_product_explan",
        "price": "dl.txt_product_price dd",
        "image": "p.img_product img",
        "url":   "p.txt_product_name a",
    },
    # ── 汎用フォールバック ──
    {
        "label": "汎用",
        "item":    (
            "[class*='product-item'], [class*='item_box'], "
            "[class*='goods-item'], [class*='item-card'], "
            "li[class*='item'], div[class*='item']"
        ),
        "name":    "h2, h3, h4, [class*='name'], [class*='title']",
        "copy":    None,
        "price":   "[class*='price']",
        "image":   "img",
        "url":     "a",
    },
]


def _get_image_url(tag, base_url: str) -> str:
    """img タグから有効な画像URLを取得（lazy-load属性を優先）"""
    if not tag:
        return ""
    for attr in ("data-lazy", "data-src", "data-original", "src"):
        val = tag.get(attr, "")
        if val and not val.startswith("data:") and "loading.svg" not in val:
            return val if val.startswith("http") else base_url + val
    return ""


def _playwright_available() -> bool:
    """Playwright がインストール済みかを確認する"""
    try:
        import importlib
        importlib.import_module("playwright.sync_api")
        return True
    except ImportError:
        return False


def _scrape_with_playwright(url: str) -> tuple[str, str]:
    """
    Playwright（ヘッドレスChromium）でページをレンダリングし、
    DOMが構築された後のHTML文字列とベースURLを返す。

    Args:
        url: 取得対象のURL

    Returns:
        (rendered_html, base_url)

    Raises:
        RuntimeError: Playwright未インストール / ページ取得失敗
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        raise RuntimeError(
            "PLAYWRIGHT_NOT_INSTALLED"
        )

    # Colab / Jupyter など既存イベントループ環境への対応
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass  # 通常環境では不要なのでスキップ

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="ja-JP",
            )
            page.goto(url, timeout=30_000, wait_until="networkidle")
            # Knockout.js のバインディング完了を待つ（最大5秒）
            try:
                page.wait_for_function(
                    "document.querySelectorAll('[data-bind]').length > 0",
                    timeout=5_000,
                )
            except PWTimeout:
                pass  # data-bind がなくても続行
            html = page.content()
            browser.close()
        base_url = "/".join(url.split("/")[:3])
        return html, base_url
    except PWTimeout:
        raise RuntimeError("ページの読み込みがタイムアウトしました（30秒）。")
    except Exception as e:
        raise RuntimeError(f"Playwright でのページ取得に失敗しました: {e}")


def _is_js_rendered(soup: BeautifulSoup) -> bool:
    """
    ページが JavaScript（Knockout.js など）で動的レンダリングされているか判定する。

    判定条件（いずれか1つでも True なら JS レンダリングとみなす）:
    - <script> に knockout / ko.applyBindings が含まれる
    - data-bind 属性を持つ要素が存在する
    - Vue / React / Angular などの主要 SPA フレームワークの痕跡
    """
    scripts = " ".join(s.get_text() for s in soup.find_all("script"))
    js_patterns = [
        r"knockout", r"ko\.applyBindings", r"ko\.observable",   # Knockout.js
        r"new Vue\(", r"createApp\(",                             # Vue
        r"ReactDOM\.render", r"createRoot\(",                     # React
        r"ng-app", r"angular\.module",                            # Angular
    ]
    for pat in js_patterns:
        if re.search(pat, scripts, re.IGNORECASE):
            return True
    # data-bind 属性が 3 つ以上あれば Knockout.js の可能性が高い
    if len(soup.find_all(attrs={"data-bind": True})) >= 3:
        return True
    return False


def scrape_product_list(url: str) -> list[dict]:
    """
    商品一覧ページから商品情報をスクレイプする。
    FutureShop / カラーミーショップ / BASE / makeshop / 汎用 に対応。

    Args:
        url: 商品一覧ページのURL

    Returns:
        list[dict]: 商品情報のリスト
            [{"name": str, "copy": str, "price": str,
              "image_url": str, "product_url": str}, ...]
    Raises:
        RuntimeError: ページ取得失敗 または 商品が見つからない場合
    """
    # ── スクレイピング可否チェック ──
    perm_status, perm_msg = check_scraping_permission(url)
    if perm_status == "blocked":
        raise RuntimeError(f"🚫 スクレイピング禁止\n\n{perm_msg}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }

    try:
        res = requests.get(url, headers=headers, timeout=20)
        res.raise_for_status()
        res.encoding = res.apparent_encoding or "utf-8"
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"ページの取得に失敗しました: {e}")

    soup = BeautifulSoup(res.text, "html.parser")
    base_url = "/".join(url.split("/")[:3])

    # ── JS動的レンダリング検出 → Playwright で自動リトライ ──
    if _is_js_rendered(soup):
        try:
            rendered_html, base_url = _scrape_with_playwright(url)
            soup = BeautifulSoup(rendered_html, "html.parser")
        except RuntimeError as e:
            err = str(e)
            if err == "PLAYWRIGHT_NOT_INSTALLED":
                raise RuntimeError("NEED_FILE_UPLOAD")
            raise RuntimeError(f"JS動的サイトの取得に失敗しました: {e}")

    # プロファイルを順番に試して最初にヒットしたものを使う
    matched_profile = None
    matched_items = []
    for profile in _EC_PROFILES:
        candidates = soup.select(profile["item"])
        if len(candidates) >= 1:
            matched_profile = profile
            matched_items = candidates
            break

    if not matched_items:
        raise RuntimeError(
            "商品ブロックが検出できませんでした。\n"
            "対象ページが商品一覧ページであることを確認してください。"
        )

    products = []
    p = matched_profile

    for item in matched_items:
        # 商品名
        name = ""
        if p["name"]:
            tag = item.select_one(p["name"])
            if tag:
                name = tag.get_text(strip=True)
        if not name:
            continue  # 商品名が取れない要素はスキップ

        # キャッチコピー（概要として使用）
        copy = ""
        if p.get("copy"):
            tag = item.select_one(p["copy"])
            if tag:
                copy = tag.get_text(strip=True)

        # 価格
        price = ""
        if p["price"]:
            tag = item.select_one(p["price"])
            if tag:
                price = tag.get_text(strip=True)

        # 画像URL（lazy-load対応）
        img_tag = item.select_one(p["image"]) if p["image"] else None
        image_url = _get_image_url(img_tag, base_url)

        # 商品ページURL
        product_url = ""
        if p["url"]:
            a_tag = item.select_one(p["url"])
            if a_tag and a_tag.get("href"):
                href = a_tag["href"]
                product_url = href if href.startswith("http") else base_url + href

        products.append({
            "name":        name,
            "copy":        copy,
            "price":       price,
            "image_url":   image_url,
            "product_url": product_url,
        })

    if not products:
        raise RuntimeError("商品情報を抽出できませんでした。")

    return products


def scrape_product_list_from_html(html: str, base_url: str = "") -> list[dict]:
    """
    ブラウザで表示済みのHTMLソース（outerHTML貼り付け）から商品情報を抽出する。

    kubara.jp のような Knockout.js / Vue / React など JS動的レンダリングサイトで、
    DevTools や「ページのソースを表示」では取得できない場合に使用する。

    使い方:
        1. ブラウザの DevTools → Elements パネルで商品リストの親要素を右クリック
        2. 「Copy → Copy outerHTML」を選択
        3. Streamlit の「HTMLを貼り付け」欄にペースト

    Args:
        html:      貼り付けたレンダリング済みHTML文字列
        base_url:  相対URLを絶対URLに変換するためのベースURL（例: "https://www.kubara.jp"）

    Returns:
        list[dict]: scrape_product_list と同形式の商品情報リスト
    """
    if not html or not html.strip():
        raise RuntimeError("HTMLが空です。ブラウザからコピーしたHTMLを貼り付けてください。")

    soup = BeautifulSoup(html, "html.parser")

    matched_profile = None
    matched_items: list = []
    for profile in _EC_PROFILES:
        candidates = soup.select(profile["item"])
        if len(candidates) >= 1:
            matched_profile = profile
            matched_items = candidates
            break

    if not matched_items:
        raise RuntimeError(
            "貼り付けられたHTMLから商品ブロックを検出できませんでした。\n"
            "商品一覧が含まれる部分のHTMLをコピーしてください（例: <div class='l_block_inner'>〜</div>）。"
        )

    products = []
    p = matched_profile
    for item in matched_items:
        name = ""
        if p["name"]:
            tag = item.select_one(p["name"])
            if tag:
                name = tag.get_text(strip=True)
        if not name:
            continue

        copy = ""
        if p.get("copy"):
            tag = item.select_one(p["copy"])
            if tag:
                copy = tag.get_text(strip=True)

        price = ""
        if p["price"]:
            tag = item.select_one(p["price"])
            if tag:
                price = tag.get_text(strip=True)

        img_tag = item.select_one(p["image"]) if p["image"] else None
        image_url = _get_image_url(img_tag, base_url)

        product_url = ""
        if p["url"]:
            a_tag = item.select_one(p["url"])
            if a_tag and a_tag.get("href"):
                href = a_tag["href"]
                product_url = href if href.startswith("http") else (base_url + href if base_url else href)

        products.append({
            "name":        name,
            "copy":        copy,
            "price":       price,
            "image_url":   image_url,
            "product_url": product_url,
            "_profile":    p["label"],
        })

    if not products:
        raise RuntimeError("HTMLから商品情報を抽出できませんでした。")

    return products


def build_product_markdown(product: dict,
                            insert_image: bool = True,
                            insert_price: bool = True,
                            insert_copy: bool = True,
                            insert_link: bool = True) -> str:
    """スクレイプした1商品をMarkdown文字列に変換する"""
    lines = [f"## {product['name']}"]

    if insert_image and product.get("image_url"):
        if insert_link and product.get("product_url"):
            # [![alt](画像URL)](商品URL) 形式で画像クリック→商品ページへ
            lines.append(f"[![{product['name']}]({product['image_url']})]({product['product_url']})")
        else:
            lines.append(f"![{product['name']}]({product['image_url']})")

    if insert_price and product.get("price"):
        lines.append(f"**価格:** {product['price']}")

    if insert_copy and product.get("copy"):
        lines.append(f"\n{product['copy']}")

    if insert_link and product.get("product_url"):
        lines.append(f"\n[▶ 商品ページを見る]({product['product_url']})")

    return "\n".join(lines)


def render_product_scraper_panel(body_key: str, current_body: str,
                                  preview_subject: str = "",
                                  preview_customer: str = "お客様") -> str:
    """
    商品スクレイパーUIパネルを expander 内に描画する。

    【設計方針】
    - Streamlit の text_area は key が存在すると value より session_state を優先する。
      そのため差し込み後は st.session_state[text_area_key] を直接書き換えてから
      rerun することでテキストエリアの表示を更新する。
    - text_area の key は f"body_text_{body_key}" で統一し、呼び出し側も同じキーを使う。
    - expander は差し込み後も常に描画し、スクレイプ結果・商品選択UIを維持する。
    - 差し込みと同時にプレビューも自動更新する。

    Args:
        body_key:         呼び出し元ごとに一意なキー文字列
        current_body:     現在のメール本文テキスト（text_area の初期値として使用）
        preview_subject:  プレビュー自動更新用の件名
        preview_customer: プレビュー自動更新用の顧客名

    Returns:
        str: text_area に表示すべき本文
    """
    _ta_key      = f"body_text_{body_key}"       # text_area と共有するキー
    _preview_key = f"preview_html_{body_key}"    # プレビューHTMLの保存キー

    # text_area 用 session_state の初期化（初回のみ）
    if _ta_key not in st.session_state:
        st.session_state[_ta_key] = current_body

    with st.expander("🛒 商品情報を一覧から差し込む", expanded=False):

        st.caption("例: https://nishidaya.com/c/all-item/gr30")
        st.caption("※デモ版では久原本家Webサイトは未対応のため他サイトでお試しください"）

        col_url, col_btn = st.columns([4, 1])
        with col_url:
            scrape_url = st.text_input(
                "商品一覧URL",
                key=f"scrape_url_{body_key}",
                placeholder="https://example.com/shop/items",
                label_visibility="collapsed",
            )
        with col_btn:
            do_scrape = st.button(
                "🔍 取得",
                key=f"scrape_btn_{body_key}",
                use_container_width=True,
            )

        if do_scrape and scrape_url:
            with st.spinner("スクレイピング可否を確認中..."):
                perm_status, perm_msg = check_scraping_permission(scrape_url)

            if perm_status == "blocked":
                st.error(f"🚫 スクレイピング禁止\n\n{perm_msg}")
                st.session_state.pop(f"scraped_products_{body_key}", None)
            else:
                if perm_status == "warning":
                    st.warning(
                        f"⚠️ robots.txt を確認できませんでした。\n\n{perm_msg}\n\n"
                        "対象サイトの利用規約を必ずご確認のうえ、自己責任で続行してください。"
                    )
                else:
                    st.success("✅ robots.txt チェック OK — スクレイピングを実行します")

                with st.spinner("商品情報を取得中...（JS動的サイトは少し時間がかかります）"):
                    try:
                        products = scrape_product_list(scrape_url)
                        st.session_state[f"scraped_products_{body_key}"] = products
                        st.success(f"✅ {len(products)}件の商品を取得しました")
                    except RuntimeError as e:
                        st.error(str(e))
                        st.session_state.pop(f"scraped_products_{body_key}", None)

        # ─────────────────────────────────────────
        # タブ共通: 取得済み商品の選択・差し込みUI
        # ─────────────────────────────────────────
        products = st.session_state.get(f"scraped_products_{body_key}", [])
        if not products:
            # 商品未取得時はUIをここで終了（expander内なので閉じていれば問題なし）
            st.info("URLを入力して「🔍 取得」を押すと商品一覧が表示されます。")
        else:
            st.markdown("---")
            st.markdown("##### ① 差し込む商品を選択")

            product_labels = [f"{p['name']}　{p['price']}" for p in products]
            selected_labels = st.multiselect(
                "商品を選択（複数選択可）",
                options=product_labels,
                key=f"selected_products_{body_key}",
            )

            if selected_labels:
                selected_products = [
                    p for p, lbl in zip(products, product_labels) if lbl in selected_labels
                ]

                st.markdown("---")
                st.markdown("##### ② 差し込む内容を選択")
                col_img, col_price, col_copy, col_link = st.columns(4)
                with col_img:
                    opt_image = st.checkbox("画像", value=True, key=f"opt_img_{body_key}")
                with col_price:
                    opt_price = st.checkbox("価格", value=True, key=f"opt_price_{body_key}")
                with col_copy:
                    opt_copy  = st.checkbox("概要", value=True, key=f"opt_copy_{body_key}")
                with col_link:
                    opt_link  = st.checkbox("リンク", value=True, key=f"opt_link_{body_key}")

                st.markdown("---")
                st.markdown("##### ③ 選択商品プレビュー")
                for prod in selected_products:
                    with st.container(border=True):
                        c1, c2 = st.columns([1, 3])
                        with c1:
                            if prod.get("image_url"):
                                st.image(prod["image_url"], use_container_width=True)
                            else:
                                st.caption("（画像なし）")
                        with c2:
                            st.markdown(f"**{prod['name']}**")
                            if prod.get("copy"):
                                st.caption(prod["copy"])
                            if prod.get("price"):
                                st.markdown(f"💴 {prod['price']}")
                            if prod.get("product_url"):
                                st.markdown(f"[商品ページ]({prod['product_url']})")

                st.markdown("---")
                insert_position = st.radio(
                    "差し込み位置",
                    ["本文の末尾に追加", "本文の先頭に追加"],
                    key=f"insert_pos_{body_key}",
                    horizontal=True,
                )
                st.caption("💡 差し込み後、テキストエリア内で自由に位置を移動できます")

                markdown_blocks = [
                    build_product_markdown(
                        prod,
                        insert_image=opt_image,
                        insert_price=opt_price,
                        insert_copy=opt_copy,
                        insert_link=opt_link,
                    )
                    for prod in selected_products
                ]
                combined_md = "\n\n---\n\n".join(markdown_blocks)

                with st.expander("📋 差し込まれるMarkdown（確認用）"):
                    st.code(combined_md, language="markdown")

                if st.button(
                    f"✍️ 選択した {len(selected_products)} 件をメール本文に差し込む",
                    type="primary",
                    use_container_width=True,
                    key=f"insert_btn_{body_key}",
                ):
                    # テキストエリアの session_state を直接書き換える
                    current_ta_val = st.session_state.get(_ta_key, current_body)
                    if insert_position == "本文の先頭に追加":
                        new_body = combined_md + "\n\n" + current_ta_val.lstrip()
                    else:
                        new_body = current_ta_val.rstrip() + "\n\n" + combined_md

                    st.session_state[_ta_key] = new_body

                    # プレビューも同時に自動更新
                    if preview_subject or preview_customer:
                        _html = markdown_to_html(new_body)
                        st.session_state[_preview_key] = generate_html_email(
                            preview_subject, _html, preview_customer
                        )

                    st.rerun()

    # text_area の value として返す値（session_state 経由で管理）
    return st.session_state.get(_ta_key, current_body)


def markdown_to_html(text):
    """
    Markdown形式のテキストをHTMLに変換
    
    Args:
        text: Markdown形式のテキスト
    
    Returns:
        str: HTML形式のテキスト
    """
    if not text:
        return ""
    
    # 見出し（h3, h2, h1の順で処理）
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    
    # 太字（**text**）
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    
    # 斜体（*text*）
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)\*(?!\*)', r'<em>\1</em>', text)
    
    # 画像リンク（[![alt](img_url)](link_url)）→ クリッカブル画像
    text = re.sub(
        r'\[!\[([^\]]*)\]\(([^\)]+)\)\]\(([^\)]+)\)',
        r'<a href="\3" target="_blank" style="display: block; text-decoration: none;">'
        r'<img src="\2" alt="\1" style="max-width: 100%; height: auto; border-radius: 4px; margin: 20px 0; cursor: pointer;">'
        r'</a>',
        text,
    )

    # 画像単体（![alt](url)）
    text = re.sub(
        r'!\[([^\]]*)\]\(([^\)]+)\)',
        r'<img src="\2" alt="\1" style="max-width: 100%; height: auto; border-radius: 4px; margin: 20px 0;">',
        text,
    )

    # リンク（[text](url)）
    text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2" target="_blank" style="color: #3498db; text-decoration: none;">\1</a>', text)
    
    # 箇条書き処理
    lines = text.split('\n')
    in_list = False
    result = []
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- '):
            if not in_list:
                result.append('<ul style="margin: 15px 0; padding-left: 20px;">')
                in_list = True
            item = stripped[2:]
            result.append(f'<li style="margin: 8px 0;">{item}</li>')
        else:
            if in_list:
                result.append('</ul>')
                in_list = False
            result.append(line)
    
    if in_list:
        result.append('</ul>')
    
    text = '\n'.join(result)
    
    # 段落処理（<h1>, <h2>, <h3>, <ul>で始まらない行をpタグで囲む）
    lines = text.split('\n')
    html_lines = []
    
    for line in lines:
        stripped = line.strip()
        if stripped and not any(stripped.startswith(tag) for tag in ['<h1', '<h2', '<h3', '<ul', '<li', '</ul', '<img']):
            html_lines.append(f'<p style="margin: 15px 0; line-height: 1.8;">{stripped}</p>')
        else:
            html_lines.append(line)
    
    return '\n'.join(html_lines)


def generate_html_email(subject, body_html, customer_name="お客様"):
    """
    HTMLメールテンプレートを生成
    
    Args:
        subject: 件名
        body_html: HTML形式の本文
        customer_name: 顧客名
    
    Returns:
        str: 完全なHTMLメール
    """
    html_template = f"""
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject}</title>
</head>
<body style="font-family: 'Helvetica Neue', Arial, 'Hiragino Kaku Gothic ProN', 'Hiragino Sans', Meiryo, sans-serif; line-height: 1.8; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f5f5f5;">
    <div style="background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
        <div style="font-size: 18px; font-weight: bold; margin-bottom: 20px; color: #2c3e50;">{customer_name} 様</div>
        {body_html}
        <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #ecf0f1; font-size: 14px; color: #95a5a6;">
            田舎主義
        </div>
    </div>
</body>
</html>
"""
    return html_template


def send_to_lrs(request_data):
    """LRSサーバーにリクエストを送信（LLM使用時）"""
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(600)
        client_socket.connect((LRS_HOST, LRS_PORT))
        
        request_json = json.dumps(request_data, ensure_ascii=False)
        client_socket.sendall(request_json.encode("utf-8"))
        
        response_data = client_socket.recv(16384).decode("utf-8")
        client_socket.close()
        
        return json.loads(response_data)
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"LRS接続エラー: {str(e)}"
        }


def generate_local_email(request_data):
    """ローカルでメール生成（LLM不使用時）"""
    try:
        from mock_lrs import generate_mock_response
        return generate_mock_response(request_data)
    except Exception as e:
        return {
            "status": "error",
            "message": f"ローカル生成エラー: {str(e)}"
        }


def generate_direct_mail_patterns(topic, key_points):
    """
    ダイレクトメールの3パターンを生成（件名・本文のA/Bテスト用）
    シズル感と説得力を強化した汎用的な文面
    
    Args:
        topic: 商品名/トピック
        key_points: 商品の詳細・特徴
    
    Returns:
        list: 3つのメールパターン
    """
    company_name = "田舎主義"
    
    patterns = []
    
    # パターンA: 既存顧客向け（王道・感謝型）
    # 「なぜ値引きするのか」の理由付けで価値を高める
    pattern_a = {
        "pattern_name": "既存顧客向け（感謝型）",
        "subject": f"【3日間限定】普段は絶対にお値下げしない{topic}を、特別価格で。",
        "body": f"""いつも当店をご愛顧いただき、誠にありがとうございます。

本日は、日頃からお世話になっているお客様へ、特別なご案内です。

当店の「{topic}」。その最大の魅力である品質を守るため、普段は一切キャンペーンを行っておりません。

しかし今回、日頃の感謝の気持ちをどうにか形にしたいと考え、**【3日間限定】**で特別価格にてご提供させていただくことになりました。

■ 期間限定のご案内
通常価格 ⇒ 【20%OFF】
※期間限定となります。

■ {topic}の特徴
{key_points}

ぜひこのお得な機会に、もう一度{topic}をお楽しみください。

▼ ご注文はこちらから（※数量限定のためお早めにどうぞ）
[ご注文URL]

{company_name}"""
    }
    patterns.append(pattern_a)
    
    # パターンB: 見込み客向け（悩み共感型・PASONAの法則）
    # 具体的な悩みにフォーカスして共感を得る
    pattern_b = {
        "pattern_name": "見込み客向け（悩み共感型）",
        "subject": f"「もっと良いものはないかな…」と思っていませんか？",
        "body": f"""「いつも使っているものだと、なんだか物足りない」
「本当に納得できる品質のものを見つけたい」

そんなあなたに、自信を持ってお届けしたいのが当店の『{topic}』です。

■ 当店の{topic}、ここが違います
{key_points}

一度お試しいただければ、その違いに驚かれるはずです。

品質に妥協しないため、普段は一切割引キャンペーンを行っておりません。
しかし、まずはこの品質を一度体験していただきたいと考え、初めての方限定の特別なご案内をご用意しました。

【初回限定お試し価格】
今なら通常価格から20%OFFでお試しいただけます。

いつもの日常が、もっと豊かになります。
まずは一度、ご自身でご実感ください。

▼ 20%OFFでお得に試してみる
[ご注文URL]

{company_name}"""
    }
    patterns.append(pattern_b)
    
    # パターンC: 見込み客向け（実績・口コミ先行型）
    # リアルな顧客の声で信頼性を高める
    pattern_c = {
        "pattern_name": "見込み客向け（口コミ型）",
        "subject": f"97%が満足！「もう他のものに戻れない」と言われる理由とは？",
        "body": f"""当店の{topic}をお試しいただいた方の**【97%】**が、「満足」と回答しています！

【お客様から感動の声が届いています】
「一度試したら、その違いに驚きました！」
「普段キャンペーンをしていないのも納得の品質です」
「家族全員が『いつもと違う！』と大絶賛でした」

■ 選ばれ続ける理由
{key_points}

本当に良いものをお届けするため、当店では安易なセールを普段は一切行っていません。

しかし、「もっと多くの方にこの感動を味わってほしい」という想いから、今回は特別にご案内です。

【初回限定20%OFF】
今だけ、特別価格でご提供中です。

多くのお客様を満足させてきた品質を、ぜひあなたもお確かめください。

▼ 特別価格でのご注文はこちら
[ご注文URL]

{company_name}"""
    }
    patterns.append(pattern_c)
    
    return patterns


def generate_batch_emails_for_group(group_config, product_manager, selected_tones, mail_type, use_llm=False):
    """グループの設定に基づいて複数トーン × ステップメール4回分を一括生成"""
    results = {}
    
    # メール生成関数を選択
    generate_func = send_to_lrs if use_llm else generate_local_email
    
    topic = group_config['topic']
    key_points = group_config['key_points']
    
    # 関連商品を取得
    related_products = product_manager.get_related_products(topic)
    
    for tone in selected_tones:
        if mail_type == "direct":
            # ダイレクトメールの場合は1通のみ
            request_data = {
                "customer_name": "お客様",
                "topic": topic,
                "key_points": key_points,
                "tone": tone,
                "mail_type": "direct",
                "step_count": 1,
                "related_products": related_products if related_products else None
            }
            response = generate_func(request_data)
            
            if response.get("status") == "success":
                results[tone] = [response]
            else:
                results[tone] = [{"status": "error", "message": response.get("message")}]
        else:
            # ステップメールの場合は4通生成
            step_mails = []
            for step in range(1, 5):
                request_data = {
                    "customer_name": "お客様",
                    "topic": topic,
                    "key_points": key_points,
                    "tone": tone,
                    "mail_type": "step",
                    "step_count": step,
                    "related_products": related_products if step == 3 and related_products else None
                }
                response = generate_func(request_data)
                step_mails.append(response)
            
            results[tone] = step_mails
    
    return results


def open_mail_clients(recipients, subject, body):
    """複数の宛先に対してメールクライアント起動用のリンクを表示（Streamlit Cloud対応）"""
    for recipient in recipients:
        encoded_subject = urllib.parse.quote(subject)
        encoded_body = urllib.parse.quote(body)
        mailto_url = f"mailto:{recipient}?subject={encoded_subject}&body={encoded_body}"
        # webbrowser.open()の代わりに、クリックできるボタン風のリンクを表示
        st.markdown(
            f'<a href="{mailto_url}" target="_blank" style="display:inline-block; padding:8px 16px; background-color:#0068c9; color:white; text-decoration:none; border-radius:4px; margin-bottom:8px;">📧 {recipient} へのメール作成画面を開く</a>', 
            unsafe_allow_html=True
        )

def create_scheduled_email_task(task_name, scheduled_datetime, recipient, subject, body):
    """タスクスケジューラー登録（Streamlit Cloudでは非対応エラーを返すように修正）"""
    import platform
    
    # 実行環境がWindows以外（Streamlit Cloudなど）の場合はエラーを返す
    if platform.system() != "Windows":
        return False, "Streamlit Cloud環境（Linux）ではWindowsタスクスケジューラーを使用した予約送信は利用できません。"

    try:
        ps_script_path = os.path.join(os.getcwd(), f"{task_name}.ps1")
        
        ps_script_content = f"""
$outlook = New-Object -ComObject Outlook.Application
$mail = $outlook.CreateItem(0)
$mail.To = "{recipient}"
$mail.Subject = "{subject}"
$mail.Body = @"
{body}
"@
$mail.Send()
Write-Host "メール送信完了: {recipient}"
exit 0
"""
        
        with open(ps_script_path, 'w', encoding='utf-8') as f:
            f.write(ps_script_content)
        
        task_datetime = scheduled_datetime.strftime("%Y/%m/%d %H:%M")
        cmd = f'''schtasks /Create /TN "{task_name}" /TR "powershell.exe -ExecutionPolicy Bypass -File \\"{ps_script_path}\\"" /SC ONCE /SD {scheduled_datetime.strftime("%Y/%m/%d")} /ST {scheduled_datetime.strftime("%H:%M")} /F'''
        
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode == 0:
            return True, f"タスク '{task_name}' を登録しました（送信予定: {task_datetime}）"
        else:
            return False, f"タスク登録エラー: {result.stderr}"
            
    except Exception as e:
        return False, f"タスク登録エラー: {str(e)}"


def schedule_all_step_emails(customers, edited_mails, scheduled_dates, group_id):
    """ステップメール4通をすべてWindowsタスクスケジューラーに登録"""
    success_count = 0
    fail_count = 0
    messages = []
    
    step_names = ["サンクスメール", "商品紹介", "おすすめ商品", "レビュー依頼"]
    
    for customer in customers:
        customer_name = customer['name']
        recipient = customer['email']
        
        for idx, (edited_mail, scheduled_dt, step_name) in enumerate(zip(edited_mails, scheduled_dates, step_names), 1):
            # 宛名を先頭に追加
            subject = edited_mail["subject"]
            body = f"{customer_name} 様\n\n{edited_mail['body']}"
            
            # タスク名を生成（ユニーク）
            task_name = f"EmailSend_{group_id.replace(' ', '')}_{customer_name.replace(' ', '')}_{recipient.split('@')[0]}_Step{idx}_{scheduled_dt.strftime('%Y%m%d%H%M')}"
            
            # タスク登録
            success, message = create_scheduled_email_task(
                task_name=task_name,
                scheduled_datetime=scheduled_dt,
                recipient=recipient,
                subject=subject,
                body=body
            )
            
            if success:
                success_count += 1
                messages.append(f"✅ {customer_name} ({recipient}) - {step_name}: 登録成功")
            else:
                fail_count += 1
                messages.append(f"❌ {customer_name} ({recipient}) - {step_name}: {message}")
    
    return success_count, fail_count, messages


def main():
    st.set_page_config(
        page_title="田舎主義ステップメール生成システム",
        page_icon="📧",
        layout="wide"
    )
    
    st.title("📧 田舎主義ステップメール生成システム（デモ版）")
    st.markdown("---")
    
    # マネージャーの初期化
    if 'customer_manager' not in st.session_state:
        st.session_state.customer_manager = CustomerManager()
    
    if 'product_manager' not in st.session_state:
        st.session_state.product_manager = ProductManager()
    
    customer_manager = st.session_state.customer_manager
    product_manager = st.session_state.product_manager
    
    # サイドバー: 関連商品管理
    with st.sidebar:
        st.header("⚙️ 設定")
        
        with st.expander("📅 送信スケジュール設定", expanded=False):
            st.markdown("### デフォルト送信スケジュール")
            st.caption("ステップメールの送信間隔と時刻を設定できます")
            
            # 現在の設定を取得
            current_config = customer_manager.get_schedule_config()
            
            col1, col2 = st.columns([3, 2])
            
            with col1:
                step1_days = st.number_input(
                    "ステップ1（サンクスメール）",
                    min_value=0,
                    max_value=30,
                    value=current_config.get('step1_days', 0),
                    key="schedule_step1_days",
                    help="購入から何日後に送信するか"
                )
                st.caption("日後に送信")
                
                step2_days = st.number_input(
                    "ステップ2（商品紹介）",
                    min_value=0,
                    max_value=30,
                    value=current_config.get('step2_days', 2),
                    key="schedule_step2_days"
                )
                st.caption("日後に送信")
                
                step3_days = st.number_input(
                    "ステップ3（おすすめ商品）",
                    min_value=0,
                    max_value=30,
                    value=current_config.get('step3_days', 7),
                    key="schedule_step3_days"
                )
                st.caption("日後に送信")
                
                step4_days = st.number_input(
                    "ステップ4（レビュー依頼）",
                    min_value=0,
                    max_value=30,
                    value=current_config.get('step4_days', 14),
                    key="schedule_step4_days"
                )
                st.caption("日後に送信")
            
            with col2:
                # 時刻設定
                current_time = current_config.get('send_time', '10:00')
                hour, minute = map(int, current_time.split(':'))
                
                send_hour = st.number_input(
                    "送信時刻（時）",
                    min_value=0,
                    max_value=23,
                    value=hour,
                    key="schedule_hour"
                )
                
                send_minute = st.number_input(
                    "送信時刻（分）",
                    min_value=0,
                    max_value=59,
                    value=minute,
                    key="schedule_minute"
                )
                
                st.caption(f"送信時刻: {send_hour:02d}:{send_minute:02d}")
            
            if st.button("💾 スケジュール設定を保存", use_container_width=True):
                new_config = {
                    "step1_days": step1_days,
                    "step2_days": step2_days,
                    "step3_days": step3_days,
                    "step4_days": step4_days,
                    "send_time": f"{send_hour:02d}:{send_minute:02d}"
                }
                customer_manager.save_schedule_config(new_config)
                st.success("✅ スケジュール設定を保存しました")
                st.rerun()
        
        with st.expander("🔗 関連商品管理", expanded=False):
            st.markdown("### 関連商品の登録")
            
            # 既存の関連商品表示
            all_products = product_manager.get_all_products()
            if all_products:
                st.markdown("#### 登録済み商品")
                for product, related in all_products.items():
                    with st.expander(f"📦 {product}"):
                        st.write("**関連商品:**")
                        for r in related:
                            st.write(f"- {r}")
                        if st.button(f"削除", key=f"del_product_{product}"):
                            product_manager.delete_product(product)
                            st.success(f"'{product}' を削除しました")
                            st.rerun()
            
            st.markdown("---")
            st.markdown("#### 新規登録")
            
            new_product = st.text_input("商品名", key="new_product_name")
            related_1 = st.text_input("関連商品1", key="related_1")
            related_2 = st.text_input("関連商品2", key="related_2")
            related_3 = st.text_input("関連商品3", key="related_3")
            
            if st.button("関連商品を登録"):
                if new_product:
                    related_list = [r for r in [related_1, related_2, related_3] if r]
                    if related_list:
                        product_manager.add_product_relation(new_product, related_list)
                        st.success(f"'{new_product}' の関連商品を登録しました")
                        st.rerun()
                    else:
                        st.warning("関連商品を少なくとも1つ入力してください")
                else:
                    st.warning("商品名を入力してください")
    
    # ===== ステップ1: CSVアップロード =====
    st.header("ステップ1: 顧客データをCSVからアップロード")
    
    st.info("""
    📋 **CSVファイルの形式**
    
    必須列:
    - `漢字氏名`: 顧客の氏名（例: 山田太郎）
    - `メールアドレス`: 顧客のメールアドレス（例: yamada@example.com）
    
    文字コード: **Shift-JIS**  
    1行目に列名を含むCSVファイルをアップロードしてください。
    """)
    
    uploaded_file = st.file_uploader(
        "CSVファイルを選択",
        type=['csv'],
        help="漢字氏名とメールアドレスの列を含むCSVファイル（Shift-JIS）"
    )
    
    if uploaded_file is not None:
        # 一時ファイルとして保存
        temp_csv_path = f"/tmp/{uploaded_file.name}"
        with open(temp_csv_path, 'wb') as f:
            f.write(uploaded_file.getbuffer())
        
        # CSVを読み込み
        success, message, customers = customer_manager.load_from_csv(temp_csv_path)
        
        if success:
            st.success(message)
        else:
            st.error(message)
    
    st.markdown("---")
    
    # ===== ステップ2: 送信グループの作成 =====
    st.header("ステップ2: 送信グループの作成")
    
    if not customer_manager.get_customers():
        st.warning("⚠️ 先に顧客データをアップロードしてください")
    else:
        st.markdown("### ➕ 新規グループ作成")
        
        # グループIDを自動生成して表示
        next_group_id = customer_manager.generate_next_group_id()
        st.info(f"🆔 新しいグループID: **{next_group_id}**（自動採番）")
        
        # 複数トーン選択対応
        new_group_tones = st.multiselect(
            "トーン（複数選択可）",
            [
                "丁寧かつ熱意をもって",
                "フォーマルで厳格に",
                "フレンドリーで親しみやすく",
                "簡潔で要点を押さえて",
                "説得力をもって積極的に",
                "控えめで謙虚に",
                "情熱的でエネルギッシュに"
            ],
            default=["丁寧かつ熱意をもって"],
            key="new_group_tones"
        )
        
        new_group_topic = st.text_input(
            "商品名/トピック",
            placeholder="例: 讃岐うどん",
            key="new_group_topic"
        )
        
        new_group_key_points = st.text_area(
            "商品の詳細・特徴",
            placeholder="例: 本場讃岐の味、コシが強い、無添加",
            height=100,
            key="new_group_key_points"
        )
        
        st.info("💡 グループ作成後、ステップ3で顧客を割り当てることができます")
        
        if st.button("✅ グループを作成", type="primary", use_container_width=True):
            if new_group_topic and new_group_tones:
                # グループ作成（グループIDは自動採番、顧客は未割り当て）
                created_group_id = customer_manager.create_group(
                    new_group_tones,  # リストで保存
                    new_group_topic,
                    new_group_key_points
                )
                
                st.success(f"グループ '{created_group_id}' を作成しました。ステップ3で顧客を割り当ててください。")
                st.rerun()
            else:
                if not new_group_tones:
                    st.warning("トーンを少なくとも1つ選択してください")
                else:
                    st.warning("商品名は必須です")
    
    st.markdown("---")
    
    # ===== ステップ3: 既存グループ管理 =====
    st.header("ステップ3: 既存グループ管理")
    
    all_groups = customer_manager.get_all_groups()
    
    if not all_groups:
        st.info("まだグループが作成されていません。ステップ2でグループを作成してください。")
    else:
        st.markdown("### 📋 既存グループ一覧")
        
        for group_id, group_config in all_groups.items():
            with st.expander(f"📁 グループ {group_id}", expanded=True):
                # トーンがリストかどうか確認
                tones = group_config.get('tone', [])
                if isinstance(tones, str):
                    # 旧形式（文字列）の場合はリストに変換
                    tones = [tones]
                
                tones_str = "、".join(tones) if tones else "未設定"
                
                # 作成日時のフォーマット変換
                created_at = group_config.get('created_at', 'N/A')
                if created_at != 'N/A':
                    try:
                        dt = datetime.fromisoformat(created_at)
                        created_at = dt.strftime('%Y-%m-%d %H:%M:%S')
                    except:
                        pass  # フォーマット変換に失敗した場合は元の値を使用
                
                st.markdown(f"""
                **グループID:** {group_id}  
                **トーン:** {tones_str}  
                **商品名:** {group_config['topic']}  
                **詳細:** {group_config['key_points']}  
                **作成日時:** {created_at}
                """)
                
                st.markdown("---")
                
                # 顧客割り当てセクション
                st.markdown("#### 👥 顧客の割り当て")
                
                all_customers = customer_manager.get_customers()
                if all_customers:
                    # 現在このグループに割り当てられている顧客のインデックスを取得
                    current_assigned = [
                        i for i, c in enumerate(all_customers)
                        if c.get('group') == group_id
                    ]
                    
                    group_key = f"group_{hash(group_id)}"

                    selected_customers = st.multiselect(
                        "顧客を選択（複数選択可）",
                        options=range(len(all_customers)),
                        format_func=lambda i: f"{all_customers[i]['name']} ({all_customers[i]['email']})",
                        default=current_assigned,
                        key=f"{group_key}_assign"
                    )
                    
                    # 更新成功メッセージの表示（rerun後に表示）
                    success_key = f'update_success_{group_id}'
                    if success_key in st.session_state:
                        count = st.session_state[success_key]
                        st.success(f"✅ {count}名の顧客をグループ '{group_id}' に割り当てました")
                        del st.session_state[success_key]
                    
                    # このグループの顧客を表示（最新の状態を取得）
                    group_customers = customer_manager.get_customers_by_group(group_id)
                    
                    st.markdown(f"**現在の顧客数:** {len(group_customers)}名")
                    
                    if group_customers:
                        st.markdown("**割り当て済み顧客:**")
                        for customer in group_customers:
                            st.text(f"- {customer['name']} ({customer['email']})")
                    
                    col1, col2, col3 = st.columns([2, 2, 1])
                    
                    with col1:
                        if st.button("💾 顧客割り当てを更新", key=f"{group_key}_update"):
                            # 顧客割り当てを更新
                            customer_manager.clear_group_assignments(group_id)
                            customer_manager.assign_group(selected_customers, group_id)
                            
                            # multiselectのキーをクリア（rerun後に最新のdefaultが適用される）
                            multiselect_key = f"{group_key}_assign"
                            if multiselect_key in st.session_state:
                                del st.session_state[multiselect_key]
                            
                            # 成功メッセージをセッションステートに保存してrerun
                            updated_customers = customer_manager.get_customers_by_group(group_id)
                            st.session_state[success_key] = len(updated_customers)
                            st.rerun()
                    
                    with col2:
                        # メール生成ボタン（顧客が割り当てられている場合のみ有効）
                        # group_customersを使用（上で既に取得済み）
                        if len(group_customers) > 0:
                            if st.button("✉️ メール生成", key=f"generate_group_{group_id}", type="primary"):
                                st.session_state.selected_group_for_generation = group_id
                                st.rerun()
                        else:
                            st.button("✉️ メール生成", key=f"generate_group_{group_id}", disabled=True)
                            st.caption("顧客を割り当ててください")
                    
                    with col3:
                        if st.button("🗑️ 削除", key=f"delete_group_{group_id}"):
                            customer_manager.delete_group(group_id)
                            st.success(f"グループ '{group_id}' を削除しました")
                            st.rerun()
                else:
                    st.warning("顧客データがありません。ステップ1でCSVをアップロードしてください。")
    
    st.markdown("---")
    
    # ===== ステップ4: グループ選択とメール生成 =====
    if 'selected_group_for_generation' in st.session_state:
        group_id = st.session_state.selected_group_for_generation
        group_config = customer_manager.get_group(group_id)
        group_customers = customer_manager.get_customers_by_group(group_id)
        
        if not group_config:
            st.error("グループ情報が見つかりません")
        elif not group_customers:
            st.error(f"グループ '{group_id}' に顧客が割り当てられていません。ステップ2で顧客を追加してください。")
        else:
            st.header(f"ステップ4: グループ '{group_id}' のメール生成")
            
            # トーンがリストかどうか確認
            tones = group_config.get('tone', [])
            if isinstance(tones, str):
                # 旧形式（文字列）の場合はリストに変換
                tones = [tones]
            
            tones_str = "、".join(tones) if tones else "未設定"
            
            st.info(f"""
            **グループ情報:**
            - 顧客数: {len(group_customers)}名
            - トーン: {tones_str}
            - 商品名: {group_config['topic']}
            """)
            
            # メールタイプ選択
            mail_type_option = st.radio(
                "メールタイプを選択",
                ["ステップメール（4通）", "ダイレクトメール（1通）"],
                key="mail_type_selection"
            )
            
            is_step_mail = "ステップ" in mail_type_option
            
            # LLM使用オプション
            use_llm = st.checkbox(
                "🤖 LLMを使用する（lrs_service.pyが起動している必要があります）",
                value=False,
                key="use_llm_option"
            )
            
            if st.button("📝 メール生成開始", type="primary", use_container_width=True):
                with st.spinner("メールを生成中..."):
                    # ダイレクトメールの場合は3パターン生成
                    if not is_step_mail:
                        # 3パターンのダイレクトメールを生成
                        patterns = generate_direct_mail_patterns(
                            topic=group_config['topic'],
                            key_points=group_config['key_points']
                        )
                        
                        # セッションステートに保存
                        st.session_state.direct_mail_patterns = patterns
                        st.session_state.current_group = group_id
                        st.session_state.is_step_mail = False
                    else:
                        # ステップメールは従来通り
                        selected_tones = tones if tones else ["丁寧かつ熱意をもって"]
                        
                        # メール生成
                        generated_mails = generate_batch_emails_for_group(
                            group_config=group_config,
                            product_manager=product_manager,
                            selected_tones=selected_tones,
                            mail_type="step",
                            use_llm=use_llm
                        )
                        
                        # セッションステートに保存
                        st.session_state.generated_mails = generated_mails
                        st.session_state.current_group = group_id
                        st.session_state.is_step_mail = True
                    
                st.success("✅ メール生成完了!")
                st.rerun()
    
    # ===== ステップ5: ステップメールプレビュー（複数トーン対応） =====
    if "generated_mails" in st.session_state and st.session_state.is_step_mail:
        st.divider()
        st.header("ステップ5: ステップメールプレビュー")
        
        group_id = st.session_state.current_group
        group_customers = customer_manager.get_customers_by_group(group_id)
        generated_mails = st.session_state.generated_mails
        
        st.info(f"📌 グループ: **{group_id}** / 顧客数: **{len(group_customers)}名**")
        
        # 顧客選択（プレビュー用）
        if group_customers:
            st.markdown("##### 👤 プレビュー用顧客選択")
            customer_options = [f"{c['name']} ({c['email']})" for c in group_customers]
            selected_customer_idx = st.selectbox(
                "メールのプレビューを表示する顧客を選択",
                range(len(group_customers)),
                format_func=lambda i: customer_options[i],
                key="preview_customer_step"
            )
            preview_customer_name = group_customers[selected_customer_idx]['name']
            st.caption("💡 メール送信時に、選択した顧客の名前が宛名として自動挿入されます")
        else:
            preview_customer_name = "顧客名"
        
        # 複数トーンのタブを作成
        tone_tabs = st.tabs([f"トーン: {tone}" for tone in generated_mails.keys()])
        
        for tab, (tone, mails) in zip(tone_tabs, generated_mails.items()):
            with tab:
                # 4通のステップメールを表示
                step_names = ["サンクスメール", "商品紹介", "おすすめ商品", "レビュー依頼"]
                step_timings = ["購入当日（即時）", "購入から2日後", "購入から7日後", "購入から14日後"]
                
                for step_idx, (mail, step_name, timing) in enumerate(zip(mails, step_names, step_timings), 1):
                    with st.expander(f"✉️ {step_idx}通目: {step_name}（{timing}）", expanded=(step_idx == 1)):
                        if mail.get("status") == "success":
                            st.markdown(f"**件名:** {mail.get('subject', 'N/A')}")
                            
                            # 宛名プレビュー（編集不可）
                            st.markdown("**宛名（送信時に自動挿入）:**")
                            st.code(f"{preview_customer_name} 様", language="text")
                            
                            # 本文から「お客様」を削除してプレビュー
                            original_body = mail.get("body", "")
                            body_without_salutation = original_body.replace("お客様\n\n", "").replace("お客様\n", "").replace("お客様", "", 1)
                            
                            st.text_area(
                                "本文",
                                value=body_without_salutation,
                                height=220,
                                key=f"preview_body_{tone}_{step_idx}",
                                disabled=True
                            )
                        else:
                            st.error(f"エラー: {mail.get('message')}")
                
                # トーンごとの確定ボタン
                if st.button(f"✅ このトーン「{tone}」で確定", type="primary", use_container_width=True, key=f"confirm_{tone}"):
                    # 本文から「お客様」を削除して保存
                    cleaned_mails = []
                    for mail in mails:
                        if mail.get("status") == "success":
                            original_body = mail.get("body", "")
                            cleaned_body = original_body.replace("お客様\n\n", "").replace("お客様\n", "").replace("お客様", "", 1)
                            cleaned_mails.append({
                                "status": mail.get("status"),
                                "subject": mail.get("subject"),
                                "body": cleaned_body
                            })
                        else:
                            cleaned_mails.append(mail)
                    
                    st.session_state.selected_pattern = {
                        'tone': tone,
                        'mails': cleaned_mails
                    }
                    st.rerun()
        
        # 最初からボタン
        if st.button("🔄 最初から", use_container_width=True, key="reset_step_mail_preview"):
            for key in ["generated_mails", "selected_group_for_generation", "current_group", "is_step_mail"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()
    
    # ===== ステップ5: ダイレクトメールプレビュー（A/Bテスト対応） =====
    if "direct_mail_patterns" in st.session_state and not st.session_state.get('is_step_mail', True):
        st.divider()
        st.header("ステップ5: ダイレクトメールプレビュー")
        
        group_id = st.session_state.current_group
        group_customers = customer_manager.get_customers_by_group(group_id)
        patterns = st.session_state.direct_mail_patterns
        
        st.info(f"📌 グループ: **{group_id}** / 顧客数: **{len(group_customers)}名**")
        
        st.markdown("### 📊 件名・本文のA/Bテスト")
        st.caption("3つのパターンから最適なものを選択できます")
        
        # 顧客選択（プレビュー用）
        if group_customers:
            st.markdown("##### 👤 プレビュー用顧客選択")
            customer_options = [f"{c['name']} ({c['email']})" for c in group_customers]
            selected_customer_idx = st.selectbox(
                "メールのプレビューを表示する顧客を選択",
                range(len(group_customers)),
                format_func=lambda i: customer_options[i],
                key="preview_customer_direct_ab"
            )
            preview_customer_name = group_customers[selected_customer_idx]['name']
            st.caption("💡 メール送信時に、選択した顧客の名前が宛名として自動挿入されます")
        else:
            preview_customer_name = "顧客名"
        
        # 3つのパターンを表示
        for idx, pattern in enumerate(patterns, 1):
            with st.expander(f"📧 パターン{chr(64+idx)}: {pattern['pattern_name']}", expanded=(idx==1)):
                st.markdown(f"**件名:**")
                st.info(pattern['subject'])
                
                # 宛名プレビュー（編集不可）
                st.markdown("**宛名（送信時に自動挿入）:**")
                st.code(f"{preview_customer_name} 様", language="text")
                
                # 本文
                st.markdown("**本文:**")
                st.text_area(
                    "本文プレビュー",
                    value=pattern['body'],
                    height=400,
                    key=f"preview_pattern_{idx}",
                    disabled=True,
                    label_visibility="collapsed"
                )
                
                if st.button(f"✅ パターン{chr(64+idx)}で確定", type="primary", use_container_width=True, key=f"confirm_pattern_{idx}"):
                    st.session_state.selected_direct_mail = {
                        'pattern_name': pattern['pattern_name'],
                        'mail': {
                            "status": "success",
                            "subject": pattern['subject'],
                            "body": pattern['body']
                        }
                    }
                    st.rerun()
        
        # 最初からボタン
        if st.button("🔄 最初から", use_container_width=True, key="reset_direct_mail_preview_ab"):
            for key in ["direct_mail_patterns", "selected_group_for_generation", "current_group", "is_step_mail"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()
    
    # ===== ステップ6: ダイレクトメール編集・送信 =====
    if "selected_direct_mail" in st.session_state and not st.session_state.get('is_step_mail', True):
        st.divider()
        st.subheader("ステップ6: メール編集・送信")
        
        group_id = st.session_state.current_group
        group_customers = customer_manager.get_customers_by_group(group_id)
        
        mail_data = st.session_state.selected_direct_mail
        pattern_name = mail_data.get('pattern_name', 'カスタム')
        st.info(f"📌 グループ: **{group_id}** / パターン: **{pattern_name}** / 顧客数: **{len(group_customers)}名**")
        
        # 顧客選択（プレビュー用）
        if group_customers:
            st.markdown("##### 👤 プレビュー用顧客選択")
            customer_options = [f"{c['name']} ({c['email']})" for c in group_customers]
            selected_customer_idx = st.selectbox(
                "メールのプレビューを表示する顧客を選択",
                range(len(group_customers)),
                format_func=lambda i: customer_options[i],
                key="edit_preview_customer_direct"
            )
            preview_customer_name = group_customers[selected_customer_idx]['name']
        else:
            preview_customer_name = "顧客名"
        
        # 編集可能フォーム
        edited_subject = st.text_input(
            "件名（編集可能）",
            value=mail_data['mail'].get("subject", ""),
            key="edit_direct_subject"
        )
        
        # 宛名プレビュー（編集不可）
        st.markdown("**本文:**")
        st.caption("💡 メール送信時に自動的に「{顧客名} 様」が先頭に追加されます")
        st.markdown("**宛名（送信時に自動挿入）:**")
        st.code(f"{preview_customer_name} 様", language="text")
        
        # 商品スクレイパー差し込みパネル
        _direct_key = "direct"
        _EDIT_H = 500   # テキストエリアとプレビューで共通の高さ基準
        render_product_scraper_panel(
            _direct_key, mail_data['mail'].get("body", ""),
            preview_subject=edited_subject,
            preview_customer=preview_customer_name,
        )

        # ── 本文編集 ／ HTMLプレビュー を横並びに表示 ──
        col_edit, col_preview = st.columns(2)

        with col_edit:
            st.markdown("**✏️ 本文内容（編集可能・Markdown対応）**")
            with st.expander("💡 Markdown記法ガイド", expanded=False):
                st.markdown("""
                **使用できる記法:**
                - 見出し: `## 見出し`
                - 太字: `**太字**`
                - 画像: `![説明](画像URL)`
                - リンク: `[テキスト](URL)`
                - 箇条書き: `- 項目`
                """)
            edited_body = st.text_area(
                label="本文内容",
                label_visibility="collapsed",
                key=f"body_text_{_direct_key}",
                height=_EDIT_H,
                help="Markdown記法が使えます。見出し: ## 見出し / 太字: **太字** / 画像: ![説明](URL)"
            )

        with col_preview:
            st.markdown("**📱 HTMLプレビュー**")
            if st.button("🔄 プレビューを更新", key="preview_btn_direct", use_container_width=True):
                body_html = markdown_to_html(st.session_state.get(f"body_text_{_direct_key}", ""))
                st.session_state["preview_html_direct"] = generate_html_email(
                    edited_subject, body_html, preview_customer_name
                )
            # ボタン高さ約40px を引いた残りをプレビューに充てる
            if "preview_html_direct" in st.session_state:
                components.html(st.session_state["preview_html_direct"], height=_EDIT_H - 40, scrolling=True)
            else:
                st.info("✏️ 本文を編集後、「🔄 プレビューを更新」を押してください。")
        
        st.info(f"送信先: {len(group_customers)}名の顧客に送信されます")
        
        col1, col2 = st.columns([3, 1])
        
        with col1:
            if st.button("📧 メールクライアント起動", type="primary", use_container_width=True):
                for customer in group_customers:
                    # 宛名を先頭に追加
                    personalized_body = f"{customer['name']} 様\n\n{edited_body}"
                    open_mail_clients([customer['email']], edited_subject, personalized_body)
                
                st.success(f"✅ {len(group_customers)}件のメールクライアントを起動しました")
                st.balloons()
        
        with col2:
            if st.button("🔄 最初から", use_container_width=True, key="reset_direct_mail_edit"):
                for key in ["generated_mails", "selected_direct_mail", "selected_group_for_generation", "current_group", "is_step_mail"]:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
    
    # ===== ステップ6: ステップメール編集・送信予約 =====
    if "selected_pattern" in st.session_state and st.session_state.is_step_mail:
        st.divider()
        st.subheader("ステップ6: メール編集・送信予定設定")
        
        group_id = st.session_state.current_group
        group_customers = customer_manager.get_customers_by_group(group_id)
        
        pattern = st.session_state.selected_pattern
        st.info(f"📌 グループ: **{group_id}** / トーン: **{pattern['tone']}** / 顧客数: **{len(group_customers)}名**")
        
        # 顧客選択（プレビュー用）
        if group_customers:
            st.markdown("##### 👤 プレビュー用顧客選択")
            customer_options = [f"{c['name']} ({c['email']})" for c in group_customers]
            selected_customer_idx = st.selectbox(
                "メールのプレビューを表示する顧客を選択",
                range(len(group_customers)),
                format_func=lambda i: customer_options[i],
                key="edit_preview_customer_step"
            )
            preview_customer_name = group_customers[selected_customer_idx]['name']
            st.caption("💡 メール送信時に、各顧客の名前が宛名として自動挿入されます")
        else:
            preview_customer_name = "顧客名"
        
        # Markdown記法の説明
        with st.expander("💡 Markdown記法ガイド", expanded=False):
            st.markdown("""
            **使用できる記法:**
            - 見出し: `## 見出し`
            - 太字: `**太字**`
            - 画像: `![説明](画像URL)`
            - リンク: `[テキスト](URL)`
            - 箇条書き: `- 項目`
            
            **例:**
            ```
            ## 期間限定のご案内
            
            **日頃の感謝**を込めて、特別なご提案です。
            
            ![商品画像](https://example.com/image.jpg)
            
            - 特徴1
            - 特徴2
            ```
            """)
        
        st.markdown("---")
        
        # 送信予定日時のデフォルト値を計算（スケジュール設定を適用）
        if "scheduled_dates" not in st.session_state:
            # グループのスケジュール設定を取得（カスタム設定またはデフォルト）
            schedule_config = customer_manager.get_group_schedule(group_id)
            
            # 送信時刻を取得
            send_time_str = schedule_config.get('send_time', '10:00')
            send_hour, send_minute = map(int, send_time_str.split(':'))
            
            today = datetime.now().replace(hour=send_hour, minute=send_minute, second=0, microsecond=0)
            
            st.session_state.scheduled_dates = [
                today + timedelta(days=schedule_config.get('step1_days', 0)),
                today + timedelta(days=schedule_config.get('step2_days', 2)),
                today + timedelta(days=schedule_config.get('step3_days', 7)),
                today + timedelta(days=schedule_config.get('step4_days', 14))
            ]
        
        # ステップメール4通を編集
        step_names = ["サンクスメール", "商品紹介", "おすすめ商品", "レビュー依頼"]
        step_timings = ["購入当日（即時）", "購入から2日後", "購入から7日後", "購入から14日後"]
        
        # 編集用のセッションステート初期化
        if "edited_mails" not in st.session_state:
            st.session_state.edited_mails = []
            for mail in pattern['mails']:
                st.session_state.edited_mails.append({
                    "subject": mail.get("subject", ""),
                    "body": mail.get("body", "")
                })
        
        for step_idx, (mail, step_name, timing) in enumerate(zip(pattern['mails'], step_names, step_timings), 1):
            with st.expander(f"✉️ {step_idx}通目: {step_name}（{timing}）", expanded=(step_idx == 1)):
                
                # 送信予定日時設定
                col_date, col_time = st.columns(2)
                
                with col_date:
                    scheduled_date = st.date_input(
                        f"送信予定日",
                        value=st.session_state.scheduled_dates[step_idx - 1].date(),
                        key=f"schedule_date_{step_idx}"
                    )
                
                with col_time:
                    scheduled_time = st.time_input(
                        f"送信予定時刻",
                        value=st.session_state.scheduled_dates[step_idx - 1].time(),
                        key=f"schedule_time_{step_idx}"
                    )
                
                # 送信予定日時を更新
                scheduled_datetime = datetime.combine(scheduled_date, scheduled_time)
                st.session_state.scheduled_dates[step_idx - 1] = scheduled_datetime
                
                st.info(f"📅 送信予定: {scheduled_datetime.strftime('%Y年%m月%d日 %H:%M')}")
                
                # メール編集
                if mail.get("status") == "success":
                    edited_subject = st.text_input(
                        "件名（編集可能）",
                        value=st.session_state.edited_mails[step_idx - 1]["subject"],
                        key=f"edit_subject_{step_idx}"
                    )
                    
                    # 宛名プレビュー（編集不可）
                    st.markdown("**本文:**")
                    st.caption("💡 メール送信時に自動的に「{顧客名} 様」が先頭に追加されます")
                    st.markdown("**宛名（送信時に自動挿入）:**")
                    st.code(f"{preview_customer_name} 様", language="text")
                    
                    # 商品スクレイパー差し込みパネル
                    _step_key = f"step_{step_idx}"
                    _STEP_H = 380   # テキストエリアとプレビューで共通の高さ基準
                    render_product_scraper_panel(
                        _step_key, st.session_state.edited_mails[step_idx - 1]["body"],
                        preview_subject=edited_subject,
                        preview_customer=preview_customer_name,
                    )

                    # ── 本文編集 ／ HTMLプレビュー を横並びに表示 ──
                    col_edit, col_preview = st.columns(2)

                    with col_edit:
                        st.markdown("**✏️ 本文内容（編集可能・Markdown対応）**")
                        edited_body = st.text_area(
                            label="本文内容",
                            label_visibility="collapsed",
                            key=f"body_text_{_step_key}",
                            height=_STEP_H,
                            help="Markdown記法が使えます。見出し: ## 見出し / 太字: **太字** / 画像: ![説明](URL)"
                        )

                    with col_preview:
                        st.markdown("**📱 HTMLプレビュー**")
                        _preview_key = f"preview_html_{_step_key}"
                        if st.button("🔄 プレビューを更新", key=f"preview_btn_{_step_key}", use_container_width=True):
                            body_html = markdown_to_html(st.session_state.get(f"body_text_{_step_key}", ""))
                            st.session_state[_preview_key] = generate_html_email(
                                edited_subject, body_html, preview_customer_name
                            )
                        if _preview_key in st.session_state:
                            components.html(st.session_state[_preview_key], height=_STEP_H - 40, scrolling=True)
                        else:
                            st.info("✏️ 本文を編集後、「🔄 プレビューを更新」を押してください。")

                    # 編集内容を保存（text_area の現在値 = session_state[key] を参照）
                    st.session_state.edited_mails[step_idx - 1] = {
                        "subject": edited_subject,
                        "body": st.session_state.get(f"body_text_{_step_key}",
                                st.session_state.edited_mails[step_idx - 1]["body"])
                    }
                else:
                    st.error(f"エラー: {mail.get('message')}")
        
        st.divider()
        
        # 送信予定スケジュール表示
        st.markdown("### 📋 送信スケジュール確認")
        
        st.info(f"""
        ℹ️ **Windowsタスクスケジューラー自動登録**
        
        グループ '{group_id}' の {len(group_customers)}名の顧客全員に対して、
        指定した日時にOutlookから自動的にメールが送信されるよう設定されます。
        
        合計タスク数: {len(group_customers) * 4}件（{len(group_customers)}名 × 4通）
        """)
        
        import pandas as pd
        schedule_table = []
        for idx, (step_name, scheduled_dt) in enumerate(zip(step_names, st.session_state.scheduled_dates), 1):
            schedule_table.append({
                "ステップ": f"{idx}通目",
                "種類": step_name,
                "送信予定日時": scheduled_dt.strftime("%Y/%m/%d %H:%M"),
                "件名": st.session_state.edited_mails[idx - 1]["subject"][:30] + "..."
            })
        
        st.table(pd.DataFrame(schedule_table))
        
        # 送信方法選択
        col1, col2 = st.columns([2, 1])
        
        with col1:
            send_method = st.radio(
                "送信方法",
                ["タスクスケジューラーに登録（自動送信）", "メールクライアントで確認（手動送信）"],
                key="send_method"
            )
        
        with col2:
            if st.button("🔄 最初から", use_container_width=True, key="reset_step_mail_edit"):
                for key in ["generated_mails", "selected_pattern", "selected_group_for_generation", "current_group", "is_step_mail", "scheduled_dates", "edited_mails"]:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
        
        st.divider()
        
        # 送信ボタン
        if "タスクスケジューラー" in send_method:
            if st.button("⏰ Windowsタスクスケジューラーに登録", type="primary", use_container_width=True):
                with st.spinner("タスクスケジューラーに登録中..."):
                    success_count, fail_count, messages = schedule_all_step_emails(
                        customers=group_customers,
                        edited_mails=st.session_state.edited_mails,
                        scheduled_dates=st.session_state.scheduled_dates,
                        group_id=group_id
                    )
                    
                    total = success_count + fail_count
                    st.success(f"✅ 登録完了: {success_count}/{total}件成功")
                    
                    if fail_count > 0:
                        st.warning(f"⚠️ {fail_count}件失敗しました")
                    
                    with st.expander("📋 登録詳細", expanded=(fail_count > 0)):
                        for message in messages:
                            st.text(message)
                    
                    if success_count > 0:
                        st.balloons()
        else:
            if st.button("📧 すべてのメールクライアントを起動（4通分）", type="primary", use_container_width=True):
                for customer in group_customers:
                    for idx, edited_mail in enumerate(st.session_state.edited_mails, 1):
                        subject = edited_mail["subject"]
                        body = edited_mail["body"]
                        
                        # 宛名を先頭に追加
                        personalized_body = f"{customer['name']} 様\n\n{body}"
                        
                        # 送信予定日時を本文に追記
                        scheduled_dt = st.session_state.scheduled_dates[idx - 1]
                        body_with_schedule = f"【送信予定: {scheduled_dt.strftime('%Y年%m月%d日 %H:%M')}】\n\n{personalized_body}"
                        
                        open_mail_clients([customer['email']], subject, body_with_schedule)
                
                total_emails = len(group_customers) * 4
                st.success(f"✅ {len(group_customers)}名 × 4通 = 合計{total_emails}件のメールクライアントを起動しました")
                st.warning("⚠️ メールクライアントで手動で予約送信設定を行ってください")
                st.balloons()


if __name__ == "__main__":
    db_manager.initialize_db()
    main()
