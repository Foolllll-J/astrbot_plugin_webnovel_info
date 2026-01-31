import aiohttp
import re
from datetime import datetime
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

    async def _fetch_json(self, path):
        if not self.api_bases:
            return None
            
        async with aiohttp.ClientSession(headers=self.headers) as session:
            last_exception = None
            for base_url in self.api_bases:
                url = f"{base_url}{path}"
                try:
                    async with session.get(url, timeout=10) as resp:
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
        # tab_type=3 为小说搜索
        path = f"/api/search?key={keyword}&offset={offset}&tab_type=3"
        
        try:
            data = await self._fetch_json(path)
            if not data:
                return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []

            search_tabs = data.get("data", {}).get("search_tabs", [])
            # 寻找 tab_type 为 3 的小说标签页
            target_tab = next((t for t in search_tabs if str(t.get("tab_type")) == "3"), None)
            
            if not target_tab:
                # 如果没找到 tab_type=3，尝试取第一个有数据的 tab
                target_tab = next((t for t in search_tabs if t.get("data")), None)
            
            if not target_tab:
                return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []
                
            items = target_tab.get("data", []) or []
            results = []
            for item in items:
                book_list = item.get("book_data", [])
                for b in book_list:
                    results.append({
                        "name": b.get("book_name"),
                        "author": b.get("author"),
                        "bid": b.get("book_id"),
                        "book_id": b.get("book_id"),
                        "url": f"https://fanqienovel.com/page/{b.get('book_id')}",
                        "origin": "tomato"
                    })
            
            if return_metadata:
                has_more = target_tab.get("has_more", False)
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
        
        path = f"/api/detail?book_id={book_id}"
        
        try:
            res_json = await self._fetch_json(path)
            if not res_json:
                return None
                
            data = res_json.get("data", {}).get("data", {})
            if not data:
                return None
                
            # 18项数据映射实现
            details = {
                "name": data.get("book_name"),
                "author": data.get("author"),
                "intro": data.get("abstract", ""),
                "cover": data.get("thumb_url"),
                "status": "连载中" if str(data.get("tomato_book_status")) == "1" else "已完结",
                "word_count": f"{int(data.get('word_number', 0)) / 10000:.1f}万字" if data.get("word_number") else "未知",
                "total_chapters": data.get("serial_count"),
                "rank": None, 
                "category": data.get("category"),
                "tags": data.get("tags", "").split(",") if data.get("tags") else [],
                "rating": data.get("score", "暂无"),
                "rating_users": None,
                "collection": 0,
                "all_recommend": data.get("read_count", 0),
                "last_chapter": data.get("last_chapter_title", "见详情页"), 
                "last_update": datetime.fromtimestamp(int(data.get("last_publish_time"))).strftime('%Y-%m-%d %H:%M') if data.get("last_publish_time") else "未知",
                "first_chapter_title": data.get("first_chapter_title", "第一章"), 
                "first_chapter_content": data.get("content", ""), # 试读内容直接使用 API 的 content
                "url": book_url
            }
            return details
        except Exception as e:
            logger.error(f"[番茄] 详情获取异常: {e}")
            return None
