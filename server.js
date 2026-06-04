import express from 'express';
import cors from 'cors';
import NodeCache from 'node-cache';
import axios from 'axios';
import mongoose from 'mongoose';
import { searchAndGetEpisodes as witanimeSearch, extractStream as witanimeExtract } from './witanime.js';
import connectDB from './db.js';

const episodeSchema = new mongoose.Schema({
    title: { type: String, required: true },
    url: { type: String, required: true },
    number: { type: Number }
}, { _id: false });

const animeSchema = new mongoose.Schema({
    title: { type: String, required: true, unique: true, index: true },
    romaji: { type: String },
    episodes: [episodeSchema]
}, { timestamps: true });

const Anime = mongoose.model('Anime', animeSchema);

// Connect to MongoDB
connectDB();

const app = express();
app.use(cors());
app.use(express.json());

// الكاش: ساعتين لتفاصيل الأنمي، وساعة واحدة لروابط البث
const detailsCache = new NodeCache({ stdTTL: 7200, checkperiod: 120 });
const streamCache = new NodeCache({ stdTTL: 3600, checkperiod: 120 });

// ==========================================
// مسار 1: البحث الذكي واستخراج الحلقات
// ==========================================
app.get('/api/search-and-get-episodes', async (req, res) => {
    const title = req.query.title;
    const romaji = req.query.romaji;
    if (!title) return res.status(400).json({ success: false, error: "يرجى إرسال اسم الأنمي" });

    // فحص الكاش لتسريع الاستجابة
    if (detailsCache.has(title)) {
        console.log(`[Cache] Returning details for: ${title}`);
        return res.json({ success: true, data: detailsCache.get(title) });
    }

    try {
        // فحص قاعدة البيانات (MongoDB) أولاً
        const dbAnime = await Anime.findOne({ title: title });
        if (dbAnime) {
            console.log(`[Database] Found ${title} in MongoDB! Returning instantly.`);
            const result = { episodes: dbAnime.episodes };
            detailsCache.set(title, result);
            return res.json({ success: true, data: result });
        }

        console.log(`[Server] Not found in DB. Searching for: ${title} in Witanime...`);
        let result = await witanimeSearch(title, romaji);

        if (result && result.episodes.length > 0) {
            // حفظ البيانات الجديدة في قاعدة البيانات للمستقبل
            try {
                await Anime.create({
                    title: title,
                    romaji: romaji,
                    episodes: result.episodes
                });
                console.log(`[Database] Successfully saved ${title} to MongoDB for future use!`);
            } catch (dbErr) {
                console.error(`[Database Error] Could not save ${title}:`, dbErr.message);
            }

            detailsCache.set(title, result);
            return res.json({ success: true, data: result });
        } else {
            return res.status(404).json({ success: false, error: "لم نتمكن من العثور على هذا الأنمي في أي مصدر متاح." });
        }
    } catch (e) {
        console.error(`[Server Error in search]:`, e);
        return res.status(500).json({ success: false, error: e.message });
    }
});

// ==========================================
// مسار 2: استخراج البث الصافي (m3u8/mp4)
// ==========================================
app.get('/api/extract-stream', async (req, res) => {
    const url = req.query.url;
    if (!url) return res.status(400).json({ success: false, error: "يرجى توفير رابط الحلقة" });

    // فحص الكاش
    if (streamCache.has(url)) {
        console.log(`[Cache] Returning stream for: ${url}`);
        return res.json(streamCache.get(url));
    }

    try {
        let out;
        
        // التوجيه المباشر إلى Witanime
        console.log(`[Server] Routing to Witanime Extractor -> ${url}`);
        out = await witanimeExtract(url);

        if (out && out.success) {
            // Check if the stream needs proxying to bypass hotlinking protection
            if (!out.isIframe && out.url && (out.url.includes('.mp4') || out.url.includes('.m3u8') || out.url.includes('.txt'))) {
                const needsProxy = ['mp4upload', 'wish', 'vidbm', 'luluvdo'].some(d => out.url.includes(d) || (out.embedUrl && out.embedUrl.includes(d)));
                if (needsProxy) {
                    console.log(`[Server] Proxying stream to bypass hotlinking protection for: ${out.url}`);
                    out.originalUrl = out.url;
                    // Note: Since this is local, we use localhost. In production, this should be the public server URL.
                    const PORT = process.env.PORT || 5000;
                    out.url = `http://localhost:${PORT}/api/proxy-video?url=${encodeURIComponent(out.url)}&referer=${encodeURIComponent(out.embedUrl || "https://witanime.cyou/")}`;
                }
            }
            // حفظ الرابط المباشر في الكاش
            streamCache.set(url, out);
            return res.json(out);
        } else {
            return res.status(404).json(out || { success: false, error: "تعذر استخراج البث من هذا السيرفر." });
        }
    } catch (e) {
        console.error(`[Server Error in extract]:`, e);
        return res.status(500).json({ success: false, error: e.message });
    }
});

// ==========================================
// مسار 3: Proxy لبث الفيديو وتخطي الحماية
// ==========================================
app.get('/api/proxy-video', async (req, res) => {
    const videoUrl = req.query.url;
    const referer = req.query.referer || "";
    
    if (!videoUrl) return res.status(400).send("Missing url parameter");

    try {
        const headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        };
        
        if (referer) headers['Referer'] = referer;
        if (req.headers.range) headers['Range'] = req.headers.range;

        const response = await axios({
            method: 'get',
            url: videoUrl,
            headers: headers,
            responseType: 'stream',
            validateStatus: () => true, // Don't throw errors on 403 or 500
        });

        // Identify if the file is an HLS playlist (m3u8 or txt)
        const contentType = response.headers['content-type'] || '';
        const isPlaylist = contentType.includes('mpegurl') || videoUrl.includes('.m3u8') || videoUrl.includes('.txt');

        if (isPlaylist) {
            let body = '';
            response.data.on('data', chunk => body += chunk);
            response.data.on('end', () => {
                if (body.startsWith('#EXTM3U')) {
                    const PORT = process.env.PORT || 5000;
                    let lines = body.split('\n');
                    for (let i = 0; i < lines.length; i++) {
                        let line = lines[i].trim();
                        // Rewrite relative and absolute URLs inside the playlist to point to our proxy
                        if (line && !line.startsWith('#')) {
                            // Using URL class to resolve relative paths against the base videoUrl
                            const absUrl = new URL(line, videoUrl).href;
                            lines[i] = `http://localhost:${PORT}/api/proxy-video?url=${encodeURIComponent(absUrl)}&referer=${encodeURIComponent(referer)}`;
                        }
                    }
                    const modified = lines.join('\n');
                    
                    // Proxy safe headers
                    if (response.headers['content-type']) res.setHeader('content-type', response.headers['content-type']);
                    res.setHeader('content-length', Buffer.byteLength(modified));
                    res.status(response.status).send(modified);
                } else {
                    res.status(response.status).send(body);
                }
            });
        } else {
            // Proxy critical video streaming headers back to the client
            const headersToProxy = [
                'content-type', 'content-length', 'accept-ranges', 'content-range'
            ];
            headersToProxy.forEach(h => {
                if (response.headers[h]) {
                    res.setHeader(h, response.headers[h]);
                }
            });
            
            res.status(response.status);
            
            // Pipe the binary video stream directly to the client browser!
            response.data.pipe(res);
        }

    } catch (e) {
        console.error("[Proxy Error]:", e.message);
        if (!res.headersSent) {
            res.status(500).send("Proxy error");
        }
    }
});

const PORT = process.env.PORT || 5000;
app.listen(PORT, () => {
    console.log("=".repeat(55));
    console.log("🚀  Anido Server Node.js V2 (Multi-Source)");
    console.log(`🔓  Running on http://localhost:${PORT}`);
    console.log("=".repeat(55));
});
