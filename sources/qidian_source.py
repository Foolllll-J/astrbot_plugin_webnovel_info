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
        """强制一次性拉取所有100条结果（忽略page参数）"""
        all_records = []
        max_api_page = 5  # 最多5页=100条
        current_api_page = 1
        
        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                while current_api_page <= max_api_page:
                    search_url = f"https://m.qidian.com/so/{quote(keyword)}.html?pageNum={current_api_page}"
                    logger.info(f"正在搜索起点第{current_api_page}页: {search_url}")
                    
                    async with session.get(search_url, timeout=10) as resp:
                        content = await resp.text()
                        tree = html.fromstring(content)
                        script_node = tree.xpath("//script[@id='vite-plugin-ssr_pageContext']/text()")
                        
                        if not script_node:
                            break

                        data = json.loads(script_node[0])
                        page_data = data.get('pageContext', {}).get('pageProps', {}).get('pageData', {})
                        book_info = page_data.get('bookInfo', {})
                        records = book_info.get('records', [])
                        
                        if not records:  # 没有数据则终止
                            break
                            
                        # 合并数据
                        for r in records:
                            all_records.append({
                                "name": r.get("bName"),
                                "author": r.get("bAuth"),
                                "bid": r.get("bid"),
                                "url": f"https://m.qidian.com/book/{r.get('bid')}/",
                                "origin": "qidian"
                            })
                        
                        # 检查是否最后一页
                        if bool(book_info.get('isLast')) or len(records) < 20:
                            break
                            
                        current_api_page += 1
                
                # 最多保留100条
                all_records = all_records[:100]
                total = len(all_records)
                
                if return_metadata:
                    return {
                        "books": all_records, 
                        "total": total, 
                        "current_page": 1, 
                        "is_last": True  # 标记为最后一页，因为已拉取全部
                    }
                return all_records
                
            except Exception as e:
                logger.error(f"起点搜索异常: {e}")
                return {"books": [], "total": 0, "current_page": page, "is_last": True} if return_metadata else []

    async def get_book_details(self, book_url):
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