import aiohttp
import json
import re
from lxml import html
from urllib.parse import quote
from astrbot.api import logger
from .base_source import BaseSource

class QidianSource(BaseSource):
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
            "Referer": "https://m.qidian.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }

    async def search_book(self, keyword, page=1, return_metadata=False):
        """解析起点搜索页 - 内部处理 100 条结果封顶限制"""
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
                    
                    raw_total = book_info.get('total', len(records))
                    total = min(100, raw_total) 
                    is_last = page >= 5 or bool(book_info.get('isLast')) or len(records) < 20

                    results = []
                    for r in records:
                        results.append({
                            "name": r.get("bName"),
                            "author": r.get("bAuth"),
                            "bid": r.get("bid"),
                            "url": f"https://m.qidian.com/book/{r.get('bid')}/"
                            "origin": "qidian"
                        })

                    if return_metadata:
                        return {"books": results, "total": total, "current_page": page, "is_last": is_last}
                    return results
            except Exception as e:
                logger.error(f"起点搜索异常: {e}")
                return {"books": [], "total": 0, "current_page": page, "is_last": True} if return_metadata else []

    async def get_book_details(self, book_url):
        """解析详情页全量元数据"""
        book_url = book_url.replace("www.qidian.com", "m.qidian.com")
        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(book_url, timeout=10) as resp:
                    content = await resp.text()
                    tree = html.fromstring(content)
                    script_node = tree.xpath("//script[@id='vite-plugin-ssr_pageContext']/text()")
                    if script_node:
                        try:
                            data = json.loads(script_node[0])
                            page_data = data['pageContext']['pageProps']['pageData']
                            info = page_data['bookInfo']
                            book_extra = page_data.get('bookExtra', {})
                            chapter_data = page_data.get('chapterContentInfo', {})

                            tags = [t.get("TagName") for t in book_extra.get("ugcTagInfos", []) if t.get("TagName")]
                            
                            # 预处理缩进
                            raw_intro = info.get("desc", "").strip()
                            formatted_intro = "　　" + raw_intro if raw_intro else ""
                            
                            raw_chapter_content = chapter_data.get("firstChapterC", "").strip()
                            formatted_content = "　　" + raw_chapter_content if raw_chapter_content else ""

                            return {
                                "name": info.get("bookName"),
                                "author": info.get("authorName"),
                                "intro": formatted_intro,
                                "cover": f"https://bookcover.yuewen.com/qdbimg/349573/{info.get('bookId')}/600",
                                "status": info.get("bookStatus"),
                                "word_count": info.get("showWordsCnt"),
                                "total_chapters": page_data.get("cTCnt"),
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
                                "first_chapter_content": formatted_content,
                                "url": book_url
                            }
                        except Exception as e:
                            logger.warning(f"详情页 JSON 解析失败: {e}")
                    return None
            except Exception as e:
                logger.error(f"起点详情获取异常: {e}")
                return None