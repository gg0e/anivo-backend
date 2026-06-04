from pymongo import MongoClient
import logging
import certifi

# إعداد الـ Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# رابط الاتصال بقاعدة البيانات الذي قدمته سابقاً
MONGO_URI = "mongodb+srv://mismero:Neno1900@cluster0.lrd7lz9.mongodb.net/?appName=Cluster0"

# ==========================================
# 3. إدارة الزاحف (Crawler State)
# ==========================================
def get_crawler_state():
    try:
        state = db.crawler_state.find_one({"_id": "blkom"})
        if state:
            return state.get("last_page", 1)
        return 1
    except Exception as e:
        print(f"Error fetching crawler state: {e}")
        return 1

def update_crawler_state(page):
    try:
        db.crawler_state.update_one(
            {"_id": "blkom"},
            {"$set": {"last_page": page}},
            upsert=True
        )
    except Exception as e:
        print(f"Error updating crawler state: {e}")

# إنشاء الاتصال
try:
    client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
    # تحديد قاعدة البيانات (سيتم إنشاؤها تلقائياً إذا لم تكن موجودة)
    db = client['anivo_database']
    
    # الجداول (Collections)
    animes_col = db['animes']
    streams_col = db['streams']
    
    # التأكد من الاتصال
    client.admin.command('ping')
    logging.info("✅ تم الاتصال بقاعدة بيانات MongoDB بنجاح!")
except Exception as e:
    logging.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")

def get_anime_details(url):
    """جلب تفاصيل الأنمي من قاعدة البيانات بناءً على الرابط الخاص به (أو المعرف)"""
    return animes_col.find_one({"source_url": url}, {'_id': 0})

def save_anime_details(url, data):
    """حفظ أو تحديث تفاصيل الأنمي في قاعدة البيانات"""
    data['source_url'] = url
    animes_col.update_one(
        {"source_url": url},
        {"$set": data},
        upsert=True
    )
    logging.info(f"💾 تم حفظ تفاصيل الأنمي في قاعدة البيانات: {data.get('title')}")

def get_stream_link(episode_url):
    """جلب رابط المشاهدة من قاعدة البيانات"""
    return streams_col.find_one({"episode_url": episode_url}, {'_id': 0})

def save_stream_link(episode_url, data):
    """حفظ رابط المشاهدة في قاعدة البيانات"""
    data['episode_url'] = episode_url
    streams_col.update_one(
        {"episode_url": episode_url},
        {"$set": data},
        upsert=True
    )
    logging.info(f"💾 تم حفظ رابط البث للحلقة في قاعدة البيانات")

def search_anime_by_title(title, romaji):
    """البحث عن الأنمي في قاعدة البيانات باستخدام الاسم أو الروماجي"""
    import re
    query = {"$or": []}
    if title:
        query["$or"].append({"title": {"$regex": re.escape(title), "$options": "i"}})
    if romaji:
        query["$or"].append({"title": {"$regex": re.escape(romaji), "$options": "i"}})
        query["$or"].append({"source_url": {"$regex": re.escape(romaji.lower().replace(" ", "-")), "$options": "i"}})
    
    if not query["$or"]:
        return None
        
    return animes_col.find_one(query, {'_id': 0})
