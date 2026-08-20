import aiohttp
import re
from lxml import html
from urllib.parse import quote
from astrbot.api import logger
from .base_source import BaseSource

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "DNT": "1",
}

class CiweimaoSource(BaseSource):
    def __init__(self):
        self.base_url = "https://www.ciweimao.com"
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            headers = BROWSER_HEADERS.copy()
            headers["Referer"] = self.base_url + "/"
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def search_book(self, keyword, page=1, return_metadata=False):
        encoded_key = quote(keyword)
        search_url = f"{self.base_url}/get-search-book-list/0-0-0-0-0-0/全部/{encoded_key}/{page}"
        logger.info(f"[刺猬猫] 正在搜索: {search_url}")

        session = await self._get_session()
        try:
            async with session.get(search_url, timeout=15, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location", "")
                    logger.error(f"[刺猬猫] 请求被拦截，重定向到验证码页面: {loc}")
                    return (
                        {"books": [], "total": 0, "max_pages": 1, "is_last": True}
                        if return_metadata else []
                    )

                content = await resp.read()
                charset = resp.charset or "utf-8"
                text = content.decode(charset, errors="replace")
                tree = html.fromstring(text)

                nodes = tree.xpath("//div[@class='rank-book-list']//li")
                results = []
                for node in nodes:
                    name = node.xpath(".//p[@class='tit']/a/text() | .//a[@class='name']/text()")
                    url = node.xpath(".//p[@class='tit']/a/@href | .//a[@class='name']/@href")
                    author = node.xpath(".//p[@class='author']/a/text() | .//a[contains(@href, 'reader')]/text()")
                    if name and url:
                        book_url = url[0] if url[0].startswith("http") else self.base_url + url[0]
                        bid = None
                        bid_match = re.search(r'book/(\d+)', book_url)
                        if bid_match:
                            bid = bid_match.group(1)

                        results.append({
                            "name": name[0].strip(),
                            "author": author[0].strip() if author else "未知",
                            "url": book_url,
                            "bid": bid,
                            "origin": "ciweimao"
                        })

                if return_metadata:
                    total_str = tree.xpath("//div[@class='search-result']/span/text()")
                    total_count = int(total_str[0]) if total_str else len(results)

                    max_page_str = tree.xpath("//li[@class='pageSkip']//i/text()")
                    max_pages = int(max_page_str[0]) if max_page_str else (total_count + 9) // 10

                    return {
                        "books": results,
                        "total": total_count,
                        "max_pages": max_pages,
                        "current_page": page,
                        "is_last": page >= max_pages or len(results) < 10
                    }
                return results
        except Exception as e:
            logger.error(f"[刺猬猫] 搜索异常: {e}")
            return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []

    async def get_book_details(self, book_url):
        session = await self._get_session()
        try:
            async with session.get(book_url, timeout=15) as resp:
                content = await resp.read()
                charset = resp.charset or "utf-8"
                text = content.decode(charset, errors="replace")
                tree = html.fromstring(text)

                name = tree.xpath("//meta[@property='og:novel:book_name']/@content")
                author = tree.xpath("//meta[@property='og:novel:author']/@content")
                cover = tree.xpath("//meta[@property='og:image']/@content")
                category = tree.xpath("//meta[@property='og:novel:category']/@content")

                grade_text = "".join(tree.xpath("//p[@class='book-grade']//text()"))
                word_count = re.search(r'总字数：(\d+)', grade_text)
                collections = re.search(r'总收藏：(\d+)', grade_text)

                status_text = "".join(tree.xpath("//p[@class='update-state']//text()"))
                if "连载" in status_text:
                    status = "连载"
                elif "完结" in status_text:
                    status = "完结"
                else:
                    status = "未知"

                intro_nodes = tree.xpath("//div[contains(@class, 'book-desc')]//text()")
                update_time = tree.xpath("//p[@class='update-time']/text()")
                tags = tree.xpath("//p[@class='label-box']/span[contains(@class, 'label')]/text()")

                return {
                    "name": name[0].strip() if name else "未知",
                    "author": author[0].strip() if author else "未知",
                    "intro": "".join([line.strip() for line in intro_nodes if line.strip()]),
                    "cover": cover[0] if cover else None,
                    "status": status,
                    "word_count": f"{word_count.group(1)} 字" if word_count else "未知",
                    "category": category[0].strip() if category else "刺猬猫小说",
                    "tags": [t.strip() for t in tags if t.strip()],
                    "collection": collections.group(1) if collections else "0",
                    "last_update": update_time[0].replace("最后更新：", "").strip() if update_time else None,
                    "url": book_url,
                    "first_chapter_title": None,
                    "first_chapter_content": None
                }
        except Exception as e:
            logger.error(f"[刺猬猫] 详情解析异常: {e}")
            return None
