# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import requests
import urllib.parse
import base64
import json
import re
import logging
import concurrent.futures
import os

import cloudscraper
from bs4 import BeautifulSoup

from database import (
    get_anime_details, save_anime_details,
    get_stream_link, save_stream_link,
    search_anime_by_title
)

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
scraper = cloudscraper.create_scraper()

@app.route('/')
def home():
    # محاولة عرض ملف الواجهة إذا كان موجوداً على الخادم
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_html = os.path.join(base_dir, 'index.html')
    
    if os.path.exists(file_html):
        return send_file(file_html)
    else:
        return "HTML File not found! Please upload index.html to the server.", 404


# ==========================================
# دالة مشتركة: جلب HTML أي صفحة عبر Proxy
# ==========================================
def fetch_html_via_proxy(page_url):
    """
    يجلب HTML أي صفحة عبر proxies بالتوازي
    يستخدمها extract_stream لجلب صفحة الحلقة
    """
    proxy_urls = [
        f"https://api.allorigins.win/raw?url={urllib.parse.quote(page_url)}",
        f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(page_url)}",
        f"https://thingproxy.freeboard.io/fetch/{page_url}",
    ]

    def try_proxy(proxy_url):
        try:
            resp = requests.get(proxy_url, timeout=20,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
            if resp.status_code == 200 and len(resp.text) > 500:
                return resp.text
        except Exception:
            pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(try_proxy, p): p for p in proxy_urls}
        for future in concurrent.futures.as_completed(futures, timeout=25):
            try:
                result = future.result()
                if result:
                    return result
            except Exception:
                pass
    return None


# ==========================================
# دالة مشتركة: جلب JSON من Witanime API
# ==========================================
def fetch_witanime_api(path):
    target_url = f"https://witanime.cyou{path}"

    proxy_urls = [
        f"https://api.allorigins.win/raw?url={urllib.parse.quote(target_url)}",
        f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(target_url)}",
        f"https://thingproxy.freeboard.io/fetch/{target_url}",
        f"https://corsproxy.io/?{urllib.parse.quote(target_url)}",
    ]

    def try_proxy(proxy_url):
        try:
            resp = requests.get(proxy_url, timeout=20,
                headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            text = resp.text.strip()
            if resp.status_code == 200 and text.startswith('[') and len(text) > 5:
                data = resp.json()
                if isinstance(data, list):
                    logging.info(f"[Witanime] ✅ proxy success")
                    return data
        except Exception:
            pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(try_proxy, p): p for p in proxy_urls}
        for future in concurrent.futures.as_completed(futures, timeout=25):
            try:
                result = future.result()
                if result is not None:
                    return result
            except Exception:
                pass

    logging.warning(f"[Witanime] ❌ كل الـ proxies فشلت: {path}")
    return []


# ==========================================
# دالة: فك تشفير سيرفرات Witanime
# ==========================================
def decode_witanime_servers(html):
    """
    يفك تشفير _zG و _zH ويرجع قائمة سيرفرات مرتبة حسب الأفضلية
    الأولوية: yonaplay (عربي) ← videa ← ok.ru ← 4shared ← mp4upload
    """
    servers = []
    scripts = re.findall(r'<script.*?</script>', html, re.DOTALL | re.IGNORECASE)

    for s in scripts:
        if '_zG' not in s or '_zH' not in s:
            continue

        zG = re.search(r'var _zG=\"([^\"]+)\"', s)
        zH = re.search(r'var _zH=\"([^\"]+)\"', s)
        if not zG or not zH:
            continue

        try:
            resource_registry = json.loads(base64.b64decode(zG.group(1)).decode('utf-8'))
            config_registry   = json.loads(base64.b64decode(zH.group(1)).decode('utf-8'))

            for i in range(len(resource_registry)):
                try:
                    rd = resource_registry[i]
                    cs = config_registry[i]

                    rd = rd[::-1]
                    rd = re.sub(r'[^A-Za-z0-9+/=]', '', rd)

                    index_key    = int(base64.b64decode(cs['k']).decode('utf-8'))
                    param_offset = cs['d'][index_key]

                    decoded = base64.b64decode(rd).decode('utf-8')
                    if param_offset > 0:
                        decoded = decoded[:-param_offset]

                    if ('http' in decoded or decoded.startswith('//')) and len(decoded) > 10:
                        if decoded.startswith('//'):
                            decoded = 'https:' + decoded
                        servers.append(decoded)
                except Exception:
                    continue
        except Exception:
            continue

    if not servers:
        return []

    # ترتيب السيرفرات: yonaplay أول لأنه يدعم العربي
    priority = ['yonaplay', 'yonacdn', 'videa', 'ok.ru', '4shared',
                'mp4upload', 'wish', 'vidbm', 'luluvdo', 'dood', 'dailymotion']

    ordered = []
    for host in priority:
        matches = [s for s in servers if host in s.lower()]
        ordered.extend(matches)
    for s in servers:
        if s not in ordered:
            ordered.append(s)

    return ordered


# ==========================================
# مسار 1: تفاصيل الأنمي
# ==========================================
@app.route('/api/anime-details', methods=['GET'])
def get_anime_details_route():
    url = request.args.get('url')
    if not url:
        return jsonify({"success": False, "error": "يرجى إرسال رابط الأنمي"}), 400

    db_result = get_anime_details(url)
    if db_result:
        logging.info(f"[DB HIT] {url}")
        return jsonify({"success": True, "data": db_result})

    try:
        html = fetch_html_via_proxy(url)
        if not html:
            return jsonify({"success": False, "error": "تعذر الوصول للصفحة"}), 503

        soup = BeautifulSoup(html, 'html.parser')

        thumbnail = ""
        img_tag = soup.select_one('.thumbnail img')
        if img_tag:
            thumbnail = img_tag.get('src') or img_tag.get('data-src') or ""

        title = ""
        title_tag = soup.select_one('h1.anime-details-title')
        if title_tag:
            title = title_tag.text.strip()

        episodes_list = []
        for ep in soup.select('.episodes-card-container .episodes-card-title a'):
            episodes_list.append({"title": ep.text.strip(), "url": ep.get('href')})
        episodes_list.reverse()

        result_data = {"title": title, "thumbnail": thumbnail, "episodes": episodes_list}
        save_anime_details(url, result_data)
        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        logging.error(f"[ERROR] anime-details: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==========================================
# مسار 2: البحث عن حلقات الأنمي
# ==========================================
def search_witanime_api(title, romaji_title=None):
    search_query = romaji_title or title

    try:
        anilist_resp = requests.post(
            'https://graphql.anilist.co',
            json={
                'query': 'query($s:String){Media(search:$s,type:ANIME){title{romaji}}}',
                'variables': {'s': title}
            },
            timeout=5
        )
        if anilist_resp.status_code == 200:
            romaji = anilist_resp.json().get('data', {}).get('Media', {}).get('title', {}).get('romaji')
            if romaji:
                search_query = romaji
                logging.info(f"[AniList] → {search_query}")
    except Exception as e:
        logging.warning(f"[AniList] failed: {e}")

    for query in [
        search_query,
        " ".join(search_query.split()[:3]),
        " ".join(title.split()[:3]) if search_query != title else None
    ]:
        if not query:
            continue
        result = fetch_witanime_api(f"/wp-json/wp/v2/anime?search={urllib.parse.quote(query)}")
        if result:
            return result[0]

    return None


@app.route('/api/search-and-get-episodes', methods=['GET'])
def search_and_get_episodes():
    title  = request.args.get('title', '')
    romaji = request.args.get('romaji', '')

    if not title and not romaji:
        return jsonify({"success": False, "error": "يرجى توفير اسم الأنمي"}), 400

    db_result = search_anime_by_title(title, romaji)
    if db_result and 'episodes' in db_result:
        logging.info(f"[DB HIT] {title or romaji}")
        return jsonify({
            "success": True,
            "data": {"title": db_result.get("title", title), "episodes": db_result.get("episodes", [])}
        })

    logging.info(f"[LIVE FETCH] {title or romaji}")
    try:
        target = search_witanime_api(title, romaji)
        if not target:
            return jsonify({"success": False, "error": "الأنمي غير متوفر حالياً."}), 404

        anime_id    = target.get('id')
        anime_title = target.get('title', {}).get('rendered', '') or target.get('name', '')
        anime_link  = target.get('link', '')

        ep_data = fetch_witanime_api(f"/wp-json/wp/v2/episode?anime={anime_id}&per_page=100")
        episodes_list = [
            {"title": ep.get('title', {}).get('rendered', ''), "url": ep.get('link', '')}
            for ep in ep_data
        ]
        episodes_list.reverse()

        result_data = {"title": anime_title, "episodes": episodes_list}
        save_anime_details(anime_link, result_data)
        logging.info(f"✅ حُفظ: {anime_title} ({len(episodes_list)} حلقة)")

        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        logging.error(f"[ERROR] search: {e}")
        return jsonify({"success": False, "error": "خطأ في الاتصال."}), 500


# ==========================================
# مسار 3: رابط المشاهدة للحلقة ← الجديد
# ==========================================
@app.route('/api/extract-stream', methods=['GET'])
def extract_stream():
    episode_url = request.args.get('url')
    if not episode_url:
        return jsonify({"success": False, "error": "يرجى توفير رابط الحلقة"}), 400

    # 1. من قاعدة البيانات أولاً (فوري)
    db_stream = get_stream_link(episode_url)
    if db_stream and db_stream.get('servers'):
        logging.info(f"[DB HIT] stream: {episode_url}")
        return jsonify({
            "success": True,
            "embed_url": db_stream.get("embed_url"),
            "all_servers": db_stream.get("servers", [])
        })

    # 2. جلب صفحة الحلقة عبر proxy
    logging.info(f"[LIVE EXTRACT] {episode_url}")
    html = fetch_html_via_proxy(episode_url)

    if not html:
        return jsonify({"success": False, "error": "تعذر جلب صفحة الحلقة."}), 503

    # 3. فك تشفير السيرفرات
    servers = decode_witanime_servers(html)

    if not servers:
        logging.warning(f"[EXTRACT] لا سيرفرات في: {episode_url}")
        return jsonify({"success": False, "error": "لم يتم العثور على سيرفرات مشاهدة."}), 404

    best_embed = servers[0]
    logging.info(f"✅ [EXTRACT] {len(servers)} سيرفر | الأفضل: {best_embed}")

    # 4. حفظ في قاعدة البيانات
    save_stream_link(episode_url, {
        "embed_url": best_embed,
        "servers": servers
    })

    return jsonify({
        "success": True,
        "embed_url": best_embed,
        "all_servers": servers
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)
