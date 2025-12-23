import aiohttp
import logging
import json
import re
from lxml import html
from urllib.parse import quote
from .base_source import BaseSource

logger = logging.getLogger("QidianSource")

class QidianSource(BaseSource):
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
            "Referer": "https://m.qidian.com/",
        }

    async def search_book(self, keyword, page=1, return_metadata=False):
        """解析起点移动端搜索页 - 包含 100 条封顶限制逻辑"""
        # 起点搜索接口，pageNum 控制分页
        search_url = f"https://m.qidian.com/so/{quote(keyword)}.html?pageNum={page}"
        logger.info(f"正在搜索起点: {search_url}")

        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(search_url, timeout=10) as resp:
                    content = await resp.text()
                    tree = html.fromstring(content)
                    script_node = tree.xpath("//script[@id='vite-plugin-ssr_pageContext']/text()")
                    
                    if not script_node:
                        return {"books": [], "total": 0, "current_page": page, "is_last": True} if return_metadata else []

                    data = json.loads(script_node[0])
                    page_data = data.get('pageContext', {}).get('pageProps', {}).get('pageData', {})
                    book_info = page_data.get('bookInfo', {})
                    records = book_info.get('records', [])
                    
                    # --- 起点专属：限制搜索结果前 100 条 ---
                    raw_total = book_info.get('total', len(records))
                    total = min(100, raw_total) 
                    
                    # 判定是否为“起点定义的末页”：
                    # 1. 达到第 5 页 (100条限制)
                    # 2. 原始数据标记为末页
                    # 3. 当前记录数不足 20
                    is_last = page >= 5 or bool(book_info.get('isLast')) or len(records) < 20
                    # ------------------------------------

                    results = []
                    for r in records:
                        results.append({
                            "name": r.get("bName"),
                            "author": r.get("bAuth"),
                            "bid": r.get("bid"),
                            "url": f"https://m.qidian.com/book/{r.get('bid')}/"
                        })

                    if return_metadata:
                        return {
                            "books": results,
                            "total": total,
                            "current_page": page,
                            "is_last": is_last
                        }
                    return results
            except Exception as e:
                logger.error(f"起点搜索异常: {e}")
                return {"books": [], "total": 0, "current_page": page, "is_last": True} if return_metadata else []

    async def get_book_details(self, book_url):
        """解析详情页 - 提取包含月票排名、标签、首章试读在内的全量数据"""
        # 强制转换为移动端链接以匹配 SSR 解析逻辑
        book_url = book_url.replace("www.qidian.com", "m.qidian.com")
        
        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(book_url, timeout=10) as resp:
                    content = await resp.text()
                    tree = html.fromstring(content)
                    script_node = tree.xpath("//script[@id='vite-plugin-ssr_pageContext']/text()")
                    
                    if not script_node:
                        return None
                        
                    data = json.loads(script_node[0])
                    page_data = data.get('pageContext', {}).get('pageProps', {}).get('pageData', {})
                    
                    # 分解 JSON 各个模块
                    info = page_data.get('bookInfo', {})
                    book_extra = page_data.get('bookExtra', {})
                    chapter_data = page_data.get('chapterContentInfo', {})

                    # 提取 UGC 标签，注意起点 JSON 键名为大写 'TagName'
                    tags = [t.get("TagName") for t in book_extra.get("ugcTagInfos", []) if t.get("TagName")]

                    return {
                        "name": info.get("bookName"),
                        "author": info.get("authorName"),
                        "intro": info.get("desc"),
                        "cover": f"https://bookcover.yuewen.com/qdbimg/349573/{info.get('bookId')}/600",
                        "status": info.get("bookStatus"),
                        "word_count": info.get("showWordsCnt"),
                        "total_chapters": page_data.get("cTCnt"), # 章节数在 pageData 根下
                        "rank": page_data.get("monthTicketInfo", {}).get("rank", "未上榜"),
                        "category": f"{info.get('chanName')}·{info.get('subCateName')}",
                        "tags": tags,
                        "rating": info.get("rateInfo", {}).get("rate", "暂无"),
                        "rating_users": info.get("rateInfo", {}).get("userCount", "0"),
                        "collection": info.get("collect", 0),
                        "all_recommend": info.get("recomAll", 0),
                        "last_chapter": info.get("updChapterName"),
                        "last_update": info.get("updTime"),
                        "first_chapter_title": chapter_data.get("firstChapterT"),
                        "first_chapter_content": chapter_data.get("firstChapterC"),
                        "url": book_url
                    }
            except Exception as e:
                logger.error(f"起点详情获取异常: {e}")
                return None