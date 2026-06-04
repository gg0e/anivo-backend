import time
import schedule
import requests
import urllib.parse
import logging
import concurrent.futures
import cloudscraper
from database import save_anime_details, get_crawler_state, update_crawler_state

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

scraper = cloudscraper.create_scraper()
BASE = "https://witanime.cyou/wp-json/wp/v2"

# ==========================================
# نفس منطق الـ proxy الموجود في app.py
# ==========================================
def fetch_api(path):
    target_url = f"https://witanime.cyou{path}"

    proxy_urls = [
        f"https://api.allorigins.win/raw?url={urllib.parse.quote(target_url)}",
        f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(target_url)}",
        f"https://thingproxy.freeboard.io/fetch/{target_url}",
    ]

    def try_proxy(proxy_url):
        try:
            resp = requests.get(proxy_url, timeout=20,
                headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            text = resp.text.strip()
            if resp.status_code == 200 and (text.startswith('[') or text.startswith('{')):
                data = resp.json()
                return data
        except Exception:
            pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(try_proxy, p): p for p in proxy_urls}
        for future in concurrent.futures.as_completed(futures, timeout=25):
            try:
                result = future.result()
                if result is not None:
                    return result
            except Exception:
                pass

    return None


# ==========================================
# سحب تفاصيل أنمي واحد وحفظه
# ==========================================
def crawl_one_anime(anime):
    anime_id   = anime.get('id')
    anime_link = anime.get('link', '')
    title_raw  = anime.get('title', {})
    title      = title_raw.get('rendered', '') if isinstance(title_raw, dict) else str(title_raw)

    if not anime_id or not anime_link:
        return False

    logging.info(f"🔄 [WORKER] سحب: {title}")

    # جلب الحلقات
    ep_data = fetch_api(f"/wp-json/wp/v2/episode?anime={anime_id}&per_page=100")
    if not ep_data:
        logging.warning(f"⚠️ لا حلقات لـ: {title}")
        return False

    episodes_list = []
    for ep in ep_data:
        ep_title = ep.get('title', {}).get('rendered', '') if isinstance(ep.get('title'), dict) else ''
        ep_link  = ep.get('link', '')
        if ep_link:
            episodes_list.append({"title": ep_title, "url": ep_link})

    episodes_list.reverse()

    result_data = {
        "title": title,
        "thumbnail": anime.get('_embedded', {}).get('wp:featuredmedia', [{}])[0].get('source_url', '') if anime.get('_embedded') else '',
        "episodes": episodes_list,
        "last_updated": time.time()
    }

    save_anime_details(anime_link, result_data)
    logging.info(f"✅ [WORKER] حُفظ: {title} ({len(episodes_list)} حلقة)")
    return True


# ==========================================
# مهمة الزحف الرئيسية
# ==========================================
def bulk_crawl_job():
    current_page = get_crawler_state()
    logging.info(f"⚙️ [WORKER] بدء زحف الصفحة: {current_page}")

    data = fetch_api(f"/wp-json/wp/v2/anime?per_page=20&page={current_page}&_embed=1")

    if not data or not isinstance(data, list) or len(data) == 0:
        logging.warning(f"⚠️ [WORKER] الصفحة {current_page} فارغة، رجوع للصفحة 1")
        update_crawler_state(1)
        return

    logging.info(f"📦 [WORKER] {len(data)} أنمي في الصفحة {current_page}")

    success = 0
    for anime in data:
        if crawl_one_anime(anime):
            success += 1
        time.sleep(1.5)   # احترام لسيرفر witanime

    update_crawler_state(current_page + 1)
    logging.info(f"✅ [WORKER] انتهت الصفحة {current_page} | نجح: {success}/{len(data)} | التالية: {current_page + 1}")


# ==========================================
# تشغيل
# ==========================================
if __name__ == '__main__':
    logging.info("🚀 [WORKER] انطلق زاحف Anivo...")
    logging.info("📋 [WORKER] سيسحب 20 أنمي كل 5 دقائق تلقائياً")

    bulk_crawl_job()  # يبدأ فوراً

    schedule.every(5).minutes.do(bulk_crawl_job)

    while True:
        schedule.run_pending()
        time.sleep(1)
