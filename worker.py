import time
import schedule
import requests
from bs4 import BeautifulSoup
import logging
from database import save_anime_details

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    }

def scrape_anime(url):
    """دالة تقوم بسحب تفاصيل الأنمي وحفظها في قاعدة البيانات مباشرة"""
    logging.info(f"🔄 [WORKER] جاري سحب الأنمي: {url}")
    try:
        response = requests.get(url, headers=get_headers(), timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')

        # استخراج البوستر
        thumbnail = ""
        img_tag = soup.select_one('.thumbnail img')
        if img_tag:
            thumbnail = img_tag.get('src') or img_tag.get('data-src') or ""

        # استخراج العنوان
        title = ""
        title_tag = soup.select_one('h1.anime-details-title')
        if title_tag:
            title = title_tag.text.strip()

        # استخراج قائمة الحلقات
        episodes_list = []
        ep_links = soup.select('.episodes-card-container .episodes-card-title a')
        
        for ep in ep_links:
            ep_title = ep.text.strip()
            ep_url = ep.get('href')
            episodes_list.append({
                "title": ep_title,
                "url": ep_url
            })

        episodes_list.reverse()

        result_data = {
            "title": title,
            "thumbnail": thumbnail,
            "episodes": episodes_list,
            "last_updated": time.time()
        }

        # حفظ في قاعدة البيانات
        save_anime_details(url, result_data)
        return True
    except Exception as e:
        logging.error(f"❌ [WORKER] فشل سحب الأنمي {url}: {e}")
        return False

# قائمة الأنميات التي يراقبها النظام (مؤقتاً للبرهنة)
WATCHLIST = [
    # يمكنك وضع روابط الأنميات هنا ليقوم النظام بمراقبتها وتحديثها يومياً
]

def job():
    logging.info("⚙️ [WORKER] بدء مهمة السحب الخلفية...")
    for url in WATCHLIST:
        scrape_anime(url)
        time.sleep(3) # الانتظار بين الطلبات لتجنب الحظر
    logging.info("✅ [WORKER] انتهت مهمة السحب بنجاح.")

if __name__ == '__main__':
    logging.info("🚀 تشغيل نظام السحب الخلفي (Worker) المستقل...")
    
    # تشغيل المهمة فوراً عند البدء
    job()
    
    # جدولة المهمة لتعمل كل ساعة
    schedule.every(1).hours.do(job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
