import time
import schedule
import cloudscraper
from bs4 import BeautifulSoup
import logging
from database import save_anime_details, get_crawler_state, update_crawler_state

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

scraper = cloudscraper.create_scraper()

def scrape_anime_details(url):
    """دالة تقوم بسحب تفاصيل الأنمي وحفظها في قاعدة البيانات مباشرة"""
    logging.info(f"🔄 [WORKER] جاري سحب الأنمي: {url}")
    try:
        response = scraper.get(url, timeout=20)
        soup = BeautifulSoup(response.text, 'html.parser')

        # استخراج البوستر
        thumbnail = ""
        img_tag = soup.select_one('.thumbnail img') or soup.select_one('.poster img')
        if img_tag:
            thumbnail = img_tag.get('src') or img_tag.get('data-src') or ""

        # استخراج العنوان
        title = ""
        title_tag = soup.select_one('h1.anime-details-title') or soup.select_one('h1')
        if title_tag:
            title = title_tag.text.strip()

        # استخراج قائمة الحلقات
        episodes_list = []
        ep_links = soup.select('.episodes-card-container .episodes-card-title a') or soup.select('.episode-link')
        
        for ep in ep_links:
            ep_title = ep.text.strip()
            ep_url = ep.get('href')
            episodes_list.append({
                "title": ep_title,
                "url": ep_url
            })

        episodes_list.reverse()

        if title and episodes_list:
            result_data = {
                "title": title,
                "thumbnail": thumbnail,
                "episodes": episodes_list,
                "last_updated": time.time()
            }
            # حفظ في قاعدة البيانات
            save_anime_details(url, result_data)
            return True
        else:
            logging.warning(f"⚠️ [WORKER] لم يتم العثور على بيانات كافية في: {url}")
            return False
            
    except Exception as e:
        logging.error(f"❌ [WORKER] فشل سحب الأنمي {url}: {e}")
        return False

def bulk_crawl_job():
    logging.info("⚙️ [WORKER] بدء مهمة الزحف (Crawler) الذكية...")
    current_page = get_crawler_state()
    logging.info(f"📄 جاري فحص الصفحة رقم: {current_page}")
    
    list_url = f"https://blkom.com/anime-list?page={current_page}"
    try:
        response = scraper.get(list_url, timeout=20)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # استخراج روابط الأنمي من القائمة
        links = soup.select('a')
        anime_urls = []
        for a in links:
            href = a.get('href', '')
            # الأنميات عادة تكون تحت مسار /anime/ أو /watch/
            if '/anime/' in href and href not in anime_urls:
                if href.startswith('/'):
                    href = "https://blkom.com" + href
                anime_urls.append(href)
        
        # تحديد 20 أنمي فقط لكل جلسة تفادياً للحظر
        anime_urls = anime_urls[:20]
        
        if not anime_urls:
            logging.warning("⚠️ [WORKER] لم يتم العثور على أنميات في هذه الصفحة. ربما وصلنا للنهاية أو الموقع غير التصميم.")
            # العودة للصفحة الأولى إذا وصلنا للنهاية
            update_crawler_state(1)
            return
            
        logging.info(f"🔍 تم العثور على {len(anime_urls)} أنمي في هذه الصفحة. جاري السحب...")
        
        for url in anime_urls:
            scrape_anime_details(url)
            time.sleep(3) # انتظار 3 ثوانٍ بين كل أنمي (تفادياً للحظر)
            
        # الانتقال للصفحة التالية للمرة القادمة
        next_page = current_page + 1
        update_crawler_state(next_page)
        logging.info(f"✅ [WORKER] انتهت جلسة الزحف. سيتم سحب الصفحة {next_page} في الجلسة القادمة.")
        
    except Exception as e:
        logging.error(f"❌ [WORKER] فشل الزحف على قائمة الأنمي: {e}")

if __name__ == '__main__':
    logging.info("🚀 تشغيل الزاحف الذكي (Smart Crawler)...")
    
    # تشغيل المهمة فوراً عند البدء
    bulk_crawl_job()
    
    # الجدولة: سحب صفحة واحدة (20 أنمي) كل 10 دقائق
    schedule.every(10).minutes.do(bulk_crawl_job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
