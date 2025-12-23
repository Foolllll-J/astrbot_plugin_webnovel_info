import aiohttp
import logging
import json
import re
from lxml import html
from urllib.parse import quote

from .base_source import BaseSource

# 日志配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("QidianSource")

class QidianSource(BaseSource):
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
            "Referer": "https://m.qidian.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }

    async def search_book(self, keyword):
        """解析搜索页 - 获取书籍初步列表"""
        search_url = f"https://m.qidian.com/search?kw={quote(keyword)}"
        logger.info(f"正在搜索: {search_url}")

        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(search_url, timeout=10) as resp:
                    content = await resp.text()
                    tree = html.fromstring(content)
                    script_node = tree.xpath("//script[@id='vite-plugin-ssr_pageContext']/text()")
                    if not script_node:
                        logger.error("搜索页未找到数据脚本标签")
                        return []

                    data = json.loads(script_node[0])
                    records = data.get('pageContext', {}).get('pageProps', {}).get('pageData', {}).get('bookInfo', {}).get('records', [])

                    results = []
                    for r in records:
                        results.append({
                            "name": r.get("bName"),
                            "author": r.get("bAuth"),
                            "bid": r.get("bid"),
                            "url": f"https://m.qidian.com/book/{r.get('bid')}/"
                        })
                    return results
            except Exception as e:
                logger.error(f"搜索发生异常: {e}")
                return []

    async def get_book_details(self, book_url):
        """解析详情页数据块 - 提取全量元数据（含封面）"""
        # 统一使用移动端链接进行解析
        book_url = book_url.replace("www.qidian.com", "m.qidian.com")
        logger.info(f"正在获取详情: {book_url}")

        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(book_url, timeout=10) as resp:
                    content = await resp.text()
                    tree = html.fromstring(content)

                    script_node = tree.xpath("//script[@id='vite-plugin-ssr_pageContext']/text()")
                    if script_node:
                        try:
                            data = json.loads(script_node[0])
                            props = data.get('pageContext', {}).get('pageProps', {})
                            page_data = props.get('pageData', {})
                            info = page_data.get('bookInfo', {})
                            book_extra = page_data.get('bookExtra', {})
                            chapter_data = page_data.get('chapterContentInfo', {})

                            # 标签提取 (TagName)
                            tags = [t.get("TagName") for t in book_extra.get("ugcTagInfos", []) if t.get("TagName")]

                            # 封面图逻辑: 优先取 bookId 拼接 600 分辨率大图
                            book_id = info.get("bookId")
                            cover_url = f"https://bookcover.yuewen.com/qdbimg/349573/{book_id}/600" if book_id else ""

                            return {
                                "name": info.get("bookName"),
                                "author": info.get("authorName"),
                                "intro": info.get("desc"),
                                "cover": cover_url,
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
                                "first_chapter_content": chapter_data.get("firstChapterC"),
                                "url": book_url
                            }
                        except Exception as e:
                            logger.warning(f"JSON 解析失败: {e}")

                    return None
            except Exception as e:
                logger.error(f"获取详情异常: {e}")
                return None