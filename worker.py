import time
import schedule
import cloudscraper
import logging
from bs4 import BeautifulSoup
from database import save_anime_details, get_crawler_state, update_crawler_state

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

scraper = cloudscraper.create_scraper()
BASE_API = "https://witanime.you/wp-json/wp/v2"

def scrape_anime_details(anime_id, anime_title, anime_link):
    """دالة تقوم بسحب تفاصيل الأنمي وحلقاته من Witanime API"""
    logging.info(f"🔄 [WORKER] جاري سحب الأنمي: {anime_title}")
    try:
        # 1. جلب الصورة (Thumbnail) من صفحة الأنمي
        thumbnail = ""
        try:
            page_res = scraper.get(anime_link, timeout=15)
            soup = BeautifulSoup(page_res.text, 'html.parser')
            img_tag = soup.select_one('.anime-thumbnail img') or soup.select_one('img.img-responsive')
            if img_tag:
                thumbnail = img_tag.get('src')
        except Exception as img_e:
            logging.warning(f"⚠️ تعذر جلب صورة {anime_title}: {img_e}")

        # 2. جلب الحلقات من الـ API
        episodes_url = f"{BASE_API}/episode?anime={anime_id}&per_page=100"
        ep_res = scraper.get(episodes_url, timeout=15)
        ep_data = ep_res.json()
        
        episodes_list = []
        for item in ep_data:
            ep_title = item.get('title', {}).get('rendered', '')
            ep_link = item.get('link', '')
            # إزالة الرموز الخاصة إن وجدت
            ep_title = ep_title.encode('ascii', errors='ignore').decode('ascii') if ep_title else "حلقة"
            episodes_list.append({
                "title": ep_title,
                "url": ep_link
            })
            
        # عادة API ووردبريس يرجع الأحدث أولاً، نعكسها لتصبح من 1 إلى الأخير
        episodes_list.reverse()

        if episodes_list:
            result_data = {
                "title": anime_title,
                "thumbnail": thumbnail,
                "episodes": episodes_list,
                "last_updated": time.time()
            }
            # حفظ في قاعدة البيانات (نستخدم رابط الأنمي كمفتاح)
            save_anime_details(anime_link, result_data)
            return True
        else:
            logging.warning(f"⚠️ [WORKER] لا توجد حلقات للأنمي: {anime_title}")
            return False
            
    except Exception as e:
        logging.error(f"❌ [WORKER] فشل سحب الأنمي {anime_title}: {e}")
        return False

def bulk_crawl_job():
    logging.info("⚙️ [WORKER] بدء مهمة الزحف (Witanime API)...")
    current_page = get_crawler_state()
    logging.info(f"📄 جاري فحص الصفحة رقم: {current_page}")
    
    list_url = f"{BASE_API}/anime?per_page=20&page={current_page}"
    try:
        response = scraper.get(list_url, timeout=20)
        
        if response.status_code != 200:
            logging.warning(f"⚠️ [WORKER] الصفحة {current_page} غير موجودة أو خطأ في API. سنعود للصفحة 1.")
            update_crawler_state(1)
            return
            
        animes = response.json()
        
        if not animes:
            logging.warning("⚠️ [WORKER] لا يوجد المزيد من الأنميات. سنعود للصفحة 1.")
            update_crawler_state(1)
            return
            
        logging.info(f"🔍 تم العثور على {len(animes)} أنمي في هذه الصفحة. جاري السحب...")
        
        for anime in animes:
            anime_id = anime.get('id')
            # استخراج العنوان
            title = anime.get('title', {}).get('rendered', '') if 'title' in anime else anime.get('name', '')
            # تنظيف العنوان
            title = title.encode('ascii', errors='ignore').decode('ascii')
            
            anime_link = anime.get('link', '')
            
            if anime_id and anime_link:
                scrape_anime_details(anime_id, title, anime_link)
                time.sleep(2) # انتظار 2 ثانية بين كل أنمي لتخفيف الضغط
            
        # الانتقال للصفحة التالية للمرة القادمة
        next_page = current_page + 1
        update_crawler_state(next_page)
        logging.info(f"✅ [WORKER] انتهت جلسة الزحف. سيتم سحب الصفحة {next_page} في الجلسة القادمة.")
        
    except Exception as e:
        logging.error(f"❌ [WORKER] فشل الزحف على قائمة الأنمي: {e}")

if __name__ == '__main__':
    logging.info("🚀 تشغيل زاحف Witanime...")
    
    # تشغيل المهمة فوراً عند البدء
    bulk_crawl_job()
    
    # الجدولة: سحب صفحة واحدة (20 أنمي) كل 10 دقائق
    schedule.every(10).minutes.do(bulk_crawl_job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
