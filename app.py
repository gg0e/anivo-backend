# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import urllib.parse
import base64
import json
import re
import logging

import cloudscraper
from bs4 import BeautifulSoup
from curl_cffi import requests as cf_requests

from database import get_anime_details, save_anime_details, get_stream_link, save_stream_link, search_anime_by_title

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
scraper = cloudscraper.create_scraper()

WITANIME_DOMAINS = [
    "https://witanime.you",
    "https://witanime.cyou",
    "https://witanime.club",
]

# ==========================================
# مسار 1: استخراج تفاصيل الأنمي
# ==========================================
@app.route('/api/anime-details', methods=['GET'])
def get_anime_details_route():
    url = request.args.get('url')
    if not url:
        return jsonify({"success": False, "error": "يرجى إرسال رابط الأنمي"}), 400

    db_result = get_anime_details(url)
    if db_result:
        logging.info(f"[DB HIT] جلب البيانات من MongoDB: {url}")
        return jsonify({"success": True, "data": db_result})

    logging.info(f"[LIVE FETCH] الأنمي غير موجود بالقاعدة، سيتم سحبه فوراً: {url}")
    try:
        response = scraper.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        thumbnail = ""
        img_tag = soup.select_one('.thumbnail img')
        if img_tag:
            thumbnail = img_tag.get('src') or img_tag.get('data-src') or ""

        title = ""
        title_tag = soup.select_one('h1.anime-details-title')
        if title_tag:
            title = title_tag.text.strip()

        episodes_list = []
        ep_links = soup.select('.episodes-card-container .episodes-card-title a')
        for ep in ep_links:
            episodes_list.append({"title": ep.text.strip(), "url": ep.get('href')})
        episodes_list.reverse()

        result_data = {"title": title, "thumbnail": thumbnail, "episodes": episodes_list}
        save_anime_details(url, result_data)
        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        logging.error(f"[ERROR] خطأ أثناء جلب التفاصيل: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==========================================
# دوال مساعدة لـ Witanime API
# ==========================================

def fetch_witanime_api(path):
    """
    يجرب عدة طرق وعدة دومينات:
    1. requests مباشر (بدون proxy) — يشتغل على .you
    2. cloudscraper
    3. allorigins.win proxy
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://witanime.cyou/"
    }

    for domain in WITANIME_DOMAINS:
        api_url = f"{domain}{path}"

        # محاولة 1: طلب مباشر بسيط
        try:
            resp = requests.get(api_url, headers=headers, timeout=12)
            if resp.status_code == 200 and resp.text.strip().startswith('['):
                logging.info(f"[Witanime] ✅ Direct success: {domain}")
                return resp.json()
            logging.warning(f"[Witanime] Direct status {resp.status_code}: {domain}")
        except Exception as e:
            logging.warning(f"[Witanime] Direct failed ({domain}): {e}")

        # محاولة 2: cloudscraper
        try:
            resp = scraper.get(api_url, headers=headers, timeout=12)
            if resp.status_code == 200 and resp.text.strip().startswith('['):
                logging.info(f"[Witanime] ✅ Cloudscraper success: {domain}")
                return resp.json()
            logging.warning(f"[Witanime] Cloudscraper status {resp.status_code}: {domain}")
        except Exception as e:
            logging.warning(f"[Witanime] Cloudscraper failed ({domain}): {e}")

    # محاولة 3: allorigins.win proxy (يعمل لكن بطيء)
    primary_url = f"{WITANIME_DOMAINS[1]}{path}"
    try:
        proxy_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote(primary_url)}"
        resp = requests.get(proxy_url, timeout=25)
        if resp.status_code == 200 and resp.text.strip().startswith('['):
            logging.info(f"[Witanime] ✅ allorigins success")
            return resp.json()
        logging.warning(f"[Witanime] allorigins status: {resp.status_code}")
    except Exception as e:
        logging.warning(f"[Witanime] allorigins failed: {e}")

    return []


def search_witanime_api(title, romaji_title=None):
    search_query = romaji_title or title

    try:
        query = '''
        query ($s: String) {
          Media(search: $s, type: ANIME) {
            title { romaji }
          }
        }
        '''
        anilist_resp = requests.post(
            'https://graphql.anilist.co',
            json={'query': query, 'variables': {'s': title}},
            timeout=5
        )
        if anilist_resp.status_code == 200:
            data = anilist_resp.json()
            anilist_romaji = data.get('data', {}).get('Media', {}).get('title', {}).get('romaji')
            if anilist_romaji:
                search_query = anilist_romaji
                logging.info(f"[AniList] Translated title to: {search_query}")
    except Exception as e:
        logging.warning(f"⚠️ AniList API failed: {e}")

    path = f"/wp-json/wp/v2/anime?search={urllib.parse.quote(search_query)}"
    anime_list = fetch_witanime_api(path)

    if not anime_list:
        short_query = " ".join(search_query.split()[:3])
        path = f"/wp-json/wp/v2/anime?search={urllib.parse.quote(short_query)}"
        anime_list = fetch_witanime_api(path)

    if not anime_list and search_query != title:
        eng_short_query = " ".join(title.split()[:3])
        path = f"/wp-json/wp/v2/anime?search={urllib.parse.quote(eng_short_query)}"
        anime_list = fetch_witanime_api(path)

    if anime_list:
        return anime_list[0]
    return None


# ==========================================
# مسار 2: البحث عن حلقات الأنمي بالاسم
# ==========================================
@app.route('/api/search-and-get-episodes', methods=['GET'])
def search_and_get_episodes():
    title = request.args.get('title', '')
    romaji = request.args.get('romaji', '')

    if not title and not romaji:
        return jsonify({"success": False, "error": "يرجى توفير اسم الأنمي أو الروماجي"}), 400

    db_result = search_anime_by_title(title, romaji)
    if db_result and 'episodes' in db_result:
        logging.info(f"[DB HIT] تم العثور على الأنمي بالبحث: {title or romaji}")
        return jsonify({
            "success": True,
            "data": {
                "title": db_result.get("title", title),
                "episodes": db_result.get("episodes", [])
            }
        })

    logging.info(f"[LIVE FETCH] جاري البحث المباشر في Witanime عن: {title or romaji}")
    try:
        target = search_witanime_api(title, romaji)

        if target:
            anime_id = target.get('id')
            anime_title = target.get('title', {}).get('rendered', '') or target.get('name', '')
            anime_link = target.get('link', '')

            ep_path = f"/wp-json/wp/v2/episode?anime={anime_id}&per_page=100"
            ep_data = fetch_witanime_api(ep_path)

            episodes_list = []
            for ep in ep_data:
                episodes_list.append({
                    "title": ep.get('title', {}).get('rendered', ''),
                    "url": ep.get('link', '')
                })
            episodes_list.reverse()

            result_data = {"title": anime_title, "episodes": episodes_list}
            save_anime_details(anime_link, result_data)
            logging.info(f"✅ تم السحب المباشر وحفظ: {anime_title}")

            return jsonify({"success": True, "data": result_data})
        else:
            logging.warning(f"[MISSING] الأنمي غير موجود: {title or romaji}")
            return jsonify({"success": False, "error": "الأنمي غير متوفر في السيرفر حالياً."}), 404

    except Exception as e:
        logging.error(f"[ERROR] خطأ أثناء البحث المباشر: {str(e)}")
        return jsonify({"success": False, "error": "خطأ في الاتصال بالمصدر."}), 500


# ==========================================
# مسار 3: استخراج سيرفرات المشاهدة
# ==========================================
@app.route('/api/extract-stream', methods=['GET'])
def extract_stream():
    episode_url = request.args.get('url')
    if not episode_url:
        return jsonify({"success": False, "error": "يرجى توفير رابط الحلقة"}), 400

    db_stream = get_stream_link(episode_url)
    if db_stream:
        logging.info(f"[DB HIT] تقديم سيرفر المشاهدة من MongoDB: {episode_url}")
        return jsonify({
            "success": True,
            "embed_url": db_stream.get("embed_url"),
            "stream_url": db_stream.get("stream_url")
        })

    logging.info(f"[LIVE EXTRACT] بدء فك تشفير سيرفرات Witanime: {episode_url}")
    try:
        response = scraper.get(episode_url, timeout=15)
        html = response.text

        servers = []
        scripts = re.findall(r'<script.*?</script>', html, re.DOTALL | re.IGNORECASE)
        for s in scripts:
            if '_zG' in s and '_zH' in s:
                zG_match = re.search(r'var _zG=\"([^\"]+)\"', s)
                zH_match = re.search(r'var _zH=\"([^\"]+)\"', s)
                if zG_match and zH_match:
                    resourceRegistry = json.loads(base64.b64decode(zG_match.group(1)).decode('utf-8'))
                    configRegistry = json.loads(base64.b64decode(zH_match.group(1)).decode('utf-8'))

                    for i in range(len(resourceRegistry)):
                        resourceData = resourceRegistry[i]
                        configSettings = configRegistry[i]
                        resourceData = resourceData[::-1]
                        resourceData = re.sub(r'[^A-Za-z0-9+/=]', '', resourceData)
                        indexKey = int(base64.b64decode(configSettings['k']).decode('utf-8'))
                        paramOffset = configSettings['d'][indexKey]
                        decoded = base64.b64decode(resourceData).decode('utf-8')
                        if paramOffset > 0:
                            decoded = decoded[:-paramOffset]
                        if 'http' in decoded or '//' in decoded:
                            servers.append(decoded)

        if servers:
            best_embed = servers[0]
            if best_embed.startswith('//'):
                best_embed = 'https:' + best_embed
            save_stream_link(episode_url, {"embed_url": best_embed, "stream_url": None})
            return jsonify({
                "success": True,
                "embed_url": best_embed,
                "stream_url": None,
                "all_servers": servers
            })
        else:
            return jsonify({"success": False, "error": "لم يتم العثور على سيرفرات Witanime في هذه الحلقة."}), 404

    except Exception as e:
        logging.error(f"[ERROR] فشل فك التشفير: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    print("🚀 Anivo MongoDB Backend Server is RUNNING on port 5000...")
    app.run(debug=True, port=5000)
