import axios from 'axios';
import * as cheerio from 'cheerio';
import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';

puppeteer.use(StealthPlugin());

function decodeB64(b64Str) {
    try {
        let pad = 4 - (b64Str.length % 4);
        if (pad !== 4) b64Str += '='.repeat(pad);
        return Buffer.from(b64Str, 'base64').toString('utf8');
    } catch (e) {
        return null;
    }
}

function findVideoUrl(text) {
    const m3u8Match = text.match(/https?:\/\/[^\s'"\\<>]+\.m3u8[^\s'"\\<>]*/);
    if (m3u8Match) return m3u8Match[0];

    const mp4Match = text.match(/https?:\/\/[^\s'"\\<>]+\.mp4[^\s'"\\<>]*/);
    if (mp4Match) return mp4Match[0];

    return null;
}

export async function searchAndGetEpisodes(title, romajiTitle = null) {
    try {
        let searchQuery = romajiTitle || title;
        
        try {
            const query = `
            query ($s: String) {
              Media(search: $s, type: ANIME) {
                title {
                  romaji
                }
              }
            }`;
            const anilistResp = await axios.post('https://graphql.anilist.co', { query, variables: { s: title } }, { timeout: 10000 });
            if (anilistResp.data && anilistResp.data.data && anilistResp.data.data.Media) {
                const anilistRomaji = anilistResp.data.data.Media.title.romaji;
                if (anilistRomaji) searchQuery = anilistRomaji;
            }
        } catch (e) {
            console.warn("⚠️ AniList API failed, using fallback.");
        }
        
        async function fetchWitanimeApi(url) {
            try {
                let r = await axios.get(url, { 
                    timeout: 15000,
                    headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" }
                });
                return r.data;
            } catch (err) {
                if (err.response && (err.response.status === 403 || err.response.status === 503)) {
                    console.log(`[Cloudflare Blocked Axios] Using Puppeteer fallback for: ${url}`);
                    const browser = await puppeteer.launch({ headless: "new", args: ['--no-sandbox', '--disable-setuid-sandbox'] });
                    const page = await browser.newPage();
                    await page.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
                    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
                    const content = await page.evaluate(() => document.body.innerText);
                    await browser.close();
                    try {
                        return JSON.parse(content);
                    } catch(e) {
                        return [];
                    }
                }
                throw err;
            }
        }
        
        console.log(`[Witanime Fast] Searching for: ${searchQuery}`);
        
        let animeList = await fetchWitanimeApi(`https://witanime.you/wp-json/wp/v2/anime?search=${encodeURIComponent(searchQuery)}`);
        
        if (!animeList || animeList.length === 0) {
            let shortQuery = searchQuery.split(" ").slice(0, 3).join(" ");
            animeList = await fetchWitanimeApi(`https://witanime.you/wp-json/wp/v2/anime?search=${encodeURIComponent(shortQuery)}`);
            
            if ((!animeList || animeList.length === 0) && searchQuery !== title) {
                let engShortQuery = title.split(" ").slice(0, 3).join(" ");
                animeList = await fetchWitanimeApi(`https://witanime.you/wp-json/wp/v2/anime?search=${encodeURIComponent(engShortQuery)}`);
            }
            
            if (!animeList || animeList.length === 0) {
                return { episodes: [] };
            }
        }
        
        let targetAnime = animeList[0]; 
        let animeId = targetAnime.id;
        
        let epResp = await axios.get(`https://witanime.you/wp-json/wp/v2/episode?anime=${animeId}&per_page=100`, { timeout: 15000 });
        let episodesData = epResp.data;
        
        let episodesList = [];
        for (let ep of episodesData) {
            episodesList.push({
                title: ep.title ? ep.title.rendered : '',
                url: ep.link
            });
        }
        
        episodesList.reverse(); // 1 to latest
        
        return {
            anime_url: targetAnime.link || '',
            episodes: episodesList
        };

    } catch (error) {
        console.error("Witanime Search Error:", error.message);
        throw error;
    }
}

export async function extractStream(episodeUrl) {
    let browser = null;
    try {
        console.log(`[Witanime Extractor Fast] Fetching episode page: ${episodeUrl}`);
        let resp = await axios.get(episodeUrl, {
            headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" },
            timeout: 15000
        });
        
        let html = resp.data;
        
        let zGMatch = html.match(/var _zG=\"([^\"]+)\"/);
        let zHMatch = html.match(/var _zH=\"([^\"]+)\"/);
        
        if (!zGMatch || !zHMatch) {
            return { success: false, error: "لم يتم العثور على تشفير السيرفرات في الصفحة." };
        }
        
        let resourceRegistry = JSON.parse(decodeB64(zGMatch[1]));
        let configRegistry = JSON.parse(decodeB64(zHMatch[1]));
        
        let servers = [];
        for (let i = 0; i < resourceRegistry.length; i++) {
            let resourceData = resourceRegistry[i];
            let configSettings = configRegistry[i];
            
            resourceData = resourceData.split('').reverse().join('');
            resourceData = resourceData.replace(/[^A-Za-z0-9+/=]/g, '');
            
            let indexKey = parseInt(decodeB64(configSettings.k), 10);
            let paramOffset = configSettings.d[indexKey];
            
            let decoded = decodeB64(resourceData);
            if (paramOffset > 0) decoded = decoded.substring(0, decoded.length - paramOffset);
            
            servers.push(decoded);
        }
        
        if (servers.length === 0) return { success: false, error: "No servers decoded." };

        // Prioritize native servers OVER Yonaplay because Yonaplay is blocked by Cloudflare and has broken/404 episodes
        let prefer = ['videa', 'wish', 'vidbm', 'luluvdo', 'mp4upload', '4shared', 'ok.ru', 'dood', 'dailymotion', 'hgcloud', 'yonaplay', 'yonacdn'];
        
        let candidateServers = [];
        for (let host of prefer) {
            let matchingServers = servers.filter(s => s.toLowerCase().includes(host));
            candidateServers.push(...matchingServers);
        }
        for (let s of servers) {
            if (!candidateServers.includes(s)) candidateServers.push(s);
        }

        console.log(`[Witanime Extractor] Found ${candidateServers.length} candidate servers.`);
        console.log(`[Witanime Extractor] Launching Puppeteer to extract video...`);
        
        browser = await puppeteer.launch({ headless: "new", args: ['--no-sandbox', '--disable-setuid-sandbox'] });
        
        let finalStreamUrl = null;
        let finalEmbedUrl = candidateServers[0];

        // Try top 3 candidate servers
        for (let embedUrl of candidateServers.slice(0, 3)) {
            console.log(`[Witanime Extractor] Trying Server: ${embedUrl}`);
            const page = await browser.newPage();
            await page.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
            
            let streamUrl = null;

            page.on('response', async (response) => {
                if (streamUrl) return;
                try {
                    const u = response.url();
                    const v = findVideoUrl(u);
                    if (v && !u.toLowerCase().includes('ad') && !u.includes('blank.mp4')) {
                        streamUrl = v;
                    }
                } catch (e) {}
            });

            // Use native referer option instead of setExtraHTTPHeaders to avoid ERR_BLOCKED_BY_CLIENT on arabic URLs
            try {
                await page.goto(embedUrl, { referer: "https://witanime.you/", waitUntil: 'domcontentloaded', timeout: 30000 });
            } catch (gotoErr) {
                console.log(`[Witanime Extractor] Failed to load page ${embedUrl}: ${gotoErr.message}`);
                await page.close();
                continue;
            }
            
            let isYonaplay = embedUrl.includes('yonaplay') || embedUrl.includes('yonacdn') || embedUrl.includes('masukestin');
            
            if (isYonaplay) {
                let b64 = await page.evaluate(() => {
                    let allYonaServers = [];
                    for (let li of Array.from(document.querySelectorAll('#episode-servers li'))) {
                        let onclick = li.getAttribute('onclick') || '';
                        let m = onclick.match(/go_to_player\(['"]([^'"]+)['"]\)/);
                        if (m) {
                            let b64 = m[1];
                            let decodedHost = atob(b64);
                            if (decodedHost) allYonaServers.push({b64: b64, host: decodedHost});
                        }
                    }
                    if (allYonaServers.length > 0) {
                        let prefer = ['videa', '4shared', 'wish', 'vidbm', 'luluvdo', 'ok.ru', 'dood'];
                        for (let pref of prefer) {
                            let found = allYonaServers.find(s => s.host.toLowerCase().includes(pref));
                            if (found) return found.b64;
                        }
                        return allYonaServers[0].b64;
                    }
                    return null;
                });
                
                if (b64) {
                    console.log(`[Witanime Extractor] Extracted b64 from Yonaplay, calling go_to_player...`);
                    await page.evaluate((b64_val) => {
                        if (typeof go_to_player === 'function') go_to_player(b64_val);
                    }, b64);
                    
                    await new Promise(r => setTimeout(r, 2000));
                }
            }
            
            let jwStream = await page.evaluate(() => {
                if (typeof jwplayer === 'function') {
                    try {
                        let playlist = jwplayer().getPlaylist();
                        if (playlist && playlist.length > 0) {
                            let sources = playlist[0].sources || playlist[0].file;
                            if (Array.isArray(sources)) {
                                let best = sources.find(s => s.type === 'video/mp4' || (s.file && s.file.includes('.mp4'))) || sources[0];
                                return best.file;
                            } else if (typeof sources === 'string') {
                                return sources;
                            }
                        }
                    } catch(e) {}
                }
                return null;
            });

            if (jwStream && !jwStream.startsWith('blob:')) {
                streamUrl = jwStream;
            }

            await page.evaluate(() => {
                if (typeof jwplayer === 'function') {
                    try { jwplayer().play(); } catch(e){}
                }
                const v = document.querySelector('video');
                if (v) v.play().catch(()=>{});
                const btn = document.querySelector('[class*="play"], [id*="play"], button, .plyr__control--overlaid');
                if (btn) btn.click();
                document.body.click();
            });
            
            for (let i = 0; i < 15; i++) {
                if (streamUrl) break;
                await new Promise(r => setTimeout(r, 500));
            }
            
            if (!streamUrl) {
                const vSrc = await page.evaluate(() => {
                    const v = document.querySelector('video[src], video source[src]');
                    return v ? (v.src || v.getAttribute('src')) : null;
                });
                if (vSrc && !vSrc.startsWith('blob:')) streamUrl = vSrc;
            }

            await page.close();

            if (streamUrl) {
                finalStreamUrl = streamUrl;
                finalEmbedUrl = embedUrl;
                console.log(`[Witanime Extractor] Successfully extracted stream from ${embedUrl}`);
                break;
            } else {
                console.log(`[Witanime Extractor] Failed to extract raw stream from ${embedUrl}. Trying next...`);
            }
        }

        await browser.close();

        if (finalStreamUrl) {
            console.log(`[Witanime Extractor] FINAL SUCCESS STREAM: ${finalStreamUrl}`);
            return { success: true, url: finalStreamUrl, isIframe: false, embedUrl: finalEmbedUrl };
        } else {
            console.log(`[Witanime Extractor] Could not capture direct stream. Returning Iframe: ${finalEmbedUrl}`);
            return { success: true, url: finalEmbedUrl, isIframe: true };
        }
        
    } catch (e) {
        if (browser) await browser.close();
        console.error("Witanime Extract Error:", e.message);
        throw e;
    }
}
