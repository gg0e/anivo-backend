# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import cloudscraper
from bs4 import BeautifulSoup
import re
import logging

# استيراد دوال قاعدة البيانات
from database import get_anime_details, save_anime_details, get_stream_link, save_stream_link, search_anime_by_title

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
scraper = cloudscraper.create_scraper()

def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    }

# ==========================================
# مسار 1: استخراج تفاصيل الأنمي (الآن متصل بقاعدة البيانات)
# ==========================================
@app.route('/api/anime-details', methods=['GET'])
def get_anime_details_route():
    url = request.args.get('url')
    if not url:
        return jsonify({"success": False, "error": "يرجى إرسال رابط الأنمي"}), 400

    # 1. البحث في قاعدة البيانات أولاً (الرد في ملي ثانية!)
    db_result = get_anime_details(url)
    if db_result:
        logging.info(f"[DB HIT] جلب البيانات من MongoDB: {url}")
        return jsonify({
            "success": True,
            "data": db_result
        })

    # 2. خط الدفاع الثاني (Fallback): إذا لم يجده في قاعدة البيانات (لم يسحبه الـ Worker بعد)
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
            episodes_list.append({
                "title": ep.text.strip(),
                "url": ep.get('href')
            })
        episodes_list.reverse()

        result_data = {
            "title": title,
            "thumbnail": thumbnail,
            "episodes": episodes_list
        }

        # حفظ النتيجة في MongoDB فوراً لتتوفر للمستخدم التالي
        save_anime_details(url, result_data)

        return jsonify({
            "success": True,
            "data": result_data
        })

    except Exception as e:
        logging.error(f"[ERROR] خطأ أثناء جلب التفاصيل: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==========================================
# مسار إضافي: البحث عن حلقات الأنمي بالاسم
# ==========================================
@app.route('/api/search-and-get-episodes', methods=['GET'])
def search_and_get_episodes():
    title = request.args.get('title', '')
    romaji = request.args.get('romaji', '')
    
    if not title and not romaji:
        return jsonify({"success": False, "error": "يرجى توفير اسم الأنمي أو الروماجي"}), 400

    # 1. البحث في MongoDB
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
    else:
        # إذا لم يتم العثور عليه في قاعدة البيانات، يجب على الـ Worker سحبه لاحقاً
        # لكن في الوقت الحالي سنعيد خطأ لعدم وجوده في السيرفر الجديد
        logging.warning(f"[MISSING] الأنمي غير موجود في قاعدة البيانات: {title or romaji}")
        return jsonify({"success": False, "error": "الأنمي غير متوفر في السيرفر حالياً."}), 404


import base64
import json

# ==========================================
# مسار 2: استخراج سيرفرات المشاهدة (Witanime Logic)
# ==========================================
@app.route('/api/extract-stream', methods=['GET'])
def extract_stream():
    episode_url = request.args.get('url')
    if not episode_url:
        return jsonify({"success": False, "error": "يرجى توفير رابط الحلقة"}), 400

    # 1. التحقق من التخزين في قاعدة البيانات
    db_stream = get_stream_link(episode_url)
    if db_stream:
        logging.info(f"[DB HIT] تقديم سيرفر المشاهدة من MongoDB: {episode_url}")
        return jsonify({
            "success": True,
            "embed_url": db_stream.get("embed_url"),
            "stream_url": db_stream.get("stream_url")
        })

    # 2. الاستخراج الحي (Witanime Base64 Decoder)
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
                             
                         # التأكد من أنه رابط iframe صالح أو رابط فيديو
                         if 'http' in decoded or '//' in decoded:
                             servers.append(decoded)
                             
        if servers:
            # نأخذ أول سيرفر كسيرفر أساسي
            best_embed = servers[0]
            # بعض السيرفرات تأتي بدون http
            if best_embed.startswith('//'):
                best_embed = 'https:' + best_embed
                
            # حفظ في MongoDB
            save_stream_link(episode_url, {
                "embed_url": best_embed,
                "stream_url": None # الـ iframe سيعمل مباشرة
            })
            
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
