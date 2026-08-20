import aiohttp
import re
from datetime import datetime
from urllib.parse import quote
from .base_source import BaseSource
from astrbot.api import logger

class TomatoSource(BaseSource):
    def __init__(self, api_base=None):
        self.api_bases = []
        self.api_base = api_base
            
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    @property
    def api_base(self):
        return self.api_bases

    @api_base.setter
    def api_base(self, value):
        if isinstance(value, str):
            self.api_bases = [url.strip().rstrip('/') for url in value.split(',') if url.strip()]
        elif isinstance(value, list):
            self.api_bases = [str(url).strip().rstrip('/') for url in value if url]
        else:
            self.api_bases = []

    async def _fetch_json(self, path, method="GET", payload=None):
        if not self.api_bases:
            return None
            
        async with aiohttp.ClientSession(headers=self.headers) as session:
            last_exception = None
            for base_url in self.api_bases:
                url = f"{base_url}{path}"
                try:
                    if method == "GET":
                        async with session.get(url, timeout=30) as resp:
                            if resp.status == 200:
                                return await resp.json()
                            else:
                                logger.warning(f"[番茄] API 请求失败 {url}: Status {resp.status}")
                    else:
                        async with session.post(url, json=payload, timeout=30) as resp:
                            if resp.status == 200:
                                return await resp.json()
                            else:
                                logger.warning(f"[番茄] API 请求失败 {url}: Status {resp.status}")
                except Exception as e:
                    last_exception = e
                    logger.warning(f"[番茄] API 请求异常 {url}: {e}")
                    continue
            
            if last_exception:
                logger.error(f"[番茄] 所有 API 均请求失败，最后一次异常: {last_exception}")
            return None

    async def search_book(self, keyword, page=1, return_metadata=False):
        if not self.api_bases:
            logger.warning("[番茄] 未配置 api_base，搜索功能不可用")
            return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []
        
        offset = (page - 1) * 10
        path = f"/api/fqsearch/books?query={quote(keyword)}&offset={offset}&limit=10"
        
        try:
            data = await self._fetch_json(path)
            if not data or data.get("code") != 0:
                return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []

            search_data = data.get("data", {}) or {}
            book_list = search_data.get("books", []) or []
            results = []
            for b in book_list:
                bid = b.get("bookId")
                results.append({
                    "name": b.get("bookName"),
                    "author": b.get("author"),
                    "bid": bid,
                    "book_id": bid,
                    "url": f"https://fanqienovel.com/page/{bid}",
                    "origin": "tomato"
                })
            
            if return_metadata:
                has_more = search_data.get("hasMore", False)
                # 如果有更多，设置一个较大的总页数以允许翻页，因为 API 不直接返回总数
                max_pages = 30 if has_more else page
                return {
                    "books": results,
                    "total": len(results),
                    "max_pages": max_pages,
                    "current_page": page,
                    "is_last": not has_more
                }
            return results
        except Exception as e:
            logger.error(f"[番茄] 搜索异常: {e}")
            return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []

    async def get_book_details(self, book_url):
        if not self.api_bases:
            return None
            
        # 从 URL 中提取 book_id
        match = re.search(r'page/(\d+)', book_url)
        if not match:
            return None
        book_id = match.group(1)
        
        path = f"/api/fqnovel/book/{book_id}"
        
        try:
            res_json = await self._fetch_json(path)
            if not res_json or res_json.get("code") != 0:
                return None
                
            data = res_json.get("data", {}) or {}
            if not data:
                return None

            tags = data.get("tags") or []
            if isinstance(tags, str):
                tags = [t for t in tags.split(",") if t]

            # 封面保持原始 HEIC 地址，由 main.py 下载后本地转 JPEG
            cover = data.get("coverUrl")
                
            # 18项数据映射实现
            details = {
                "name": data.get("bookName"),
                "author": data.get("author"),
                "intro": data.get("description", ""),
                "cover": cover,
                "status": "连载中" if str(data.get("status")) == "1" else "已完结",
                "word_count": f"{int(data.get('wordNumber', 0)) / 10000:.1f}万字" if data.get("wordNumber") else "未知",
                "total_chapters": data.get("totalChapters"),
                "rank": None, 
                "category": data.get("category"),
                "tags": tags,
                "rating": data.get("score", "暂无"),
                "rating_users": None,
                "collection": self._format_count(data.get("addBookshelfCount", 0)),
                "all_recommend": self._format_count(data.get("readCount", 0)),
                "last_chapter": data.get("lastChapterTitle", "见详情页"), 
                "last_update": datetime.fromtimestamp(int(data.get("lastPublishTime"))).strftime('%Y-%m-%d %H:%M') if data.get("lastPublishTime") else "未知",
                "first_chapter_title": data.get("firstChapterTitle", "第一章"), 
                "first_chapter_content": "",
                "url": book_url
            }

            # 拉取第一章试读内容
            first_chapter_id = data.get("firstChapterItemId")
            if first_chapter_id:
                chapter = await self._fetch_chapter(book_id, first_chapter_id)
                if chapter:
                    details["first_chapter_title"] = chapter.get("chapterName", details["first_chapter_title"])
                    details["first_chapter_content"] = chapter.get("txtContent", "")
            return details
        except Exception as e:
            logger.error(f"[番茄] 详情获取异常: {e}")
            return None

    async def _fetch_chapter(self, book_id, chapter_id):
        """通过批量章节接口获取单章正文"""
        path = "/api/fqnovel/chapters/batch"
        payload = {"bookId": book_id, "chapterIds": [chapter_id]}
        try:
            res_json = await self._fetch_json(path, method="POST", payload=payload)
            if not res_json or res_json.get("code") != 0:
                return None
            chapters = (res_json.get("data", {}) or {}).get("chapters", {}) or {}
            return chapters.get(str(chapter_id))
        except Exception as e:
            logger.warning(f"[番茄] 第一章内容获取失败: {e}")
            return None

    @staticmethod
    def _format_count(value):
        """将数字格式化为万单位（如 11128238 -> 1112.8万）"""
        try:
            num = int(value)
        except (TypeError, ValueError):
            return value if value else 0
        if num >= 10000:
            return f"{num / 10000:.1f}万"
        return str(num)
