import aiohttp
import re
from lxml import html
from urllib.parse import quote
from astrbot.api import logger
from .base_source import BaseSource

class CiweimaoSource(BaseSource):
    def __init__(self):
        self.base_url = "https://www.ciweimao.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.ciweimao.com/",
        }

    async def search_book(self, keyword, page=1, return_metadata=False):
        """解析搜索页 - 提取真实总数和页数"""
        encoded_key = quote(keyword)
        search_url = f"{self.base_url}/get-search-book-list/0-0-0-0-0-0/全部/{encoded_key}/{page}"
        logger.info(f"[刺猬猫] 正在搜索: {search_url}")

        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(search_url, timeout=10) as resp:
                    content = await resp.text()
                    tree = html.fromstring(content)
                    
                    # 1. 提取书籍列表
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
                        # 2. 提取真实总条数
                        total_str = tree.xpath("//div[@class='search-result']/span/text()")
                        total_count = int(total_str[0]) if total_str else len(results)

                        # 3. 提取最大页数
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
        """解析详情页档案"""
        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(book_url, timeout=10) as resp:
                    content = await resp.text()
                    tree = html.fromstring(content)
                    
                    # 使用 Meta 标签确保核心元数据准确
                    name = tree.xpath("//meta[@property='og:novel:book_name']/@content")
                    author = tree.xpath("//meta[@property='og:novel:author']/@content")
                    cover = tree.xpath("//meta[@property='og:image']/@content")
                    category = tree.xpath("//meta[@property='og:novel:category']/@content")
                    
                    # 状态数据正则匹配
                    grade_text = "".join(tree.xpath("//p[@class='book-grade']//text()"))
                    word_count = re.search(r'总字数：(\d+)', grade_text)
                    collections = re.search(r'总收藏：(\d+)', grade_text)
                    
                    # 提取状态
                    status_text = "".join(tree.xpath("//p[@class='update-state']//text()"))
                    if "连载" in status_text:
                        status = "连载"
                    elif "完结" in status_text:
                        status = "完结"
                    else:
                        status = "未知"

                    # 简介与更新信息
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