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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Referer": "https://www.ciweimao.com/"
        }

    async def search_book(self, keyword, page=1, return_metadata=False):
        """解析刺猬猫搜索页"""
        encoded_key = quote(keyword)
        # 根据规则拼接搜索 URL
        search_url = f"{self.base_url}/get-search-book-list/0-0-0-0-0-0/全部/{encoded_key}/{page}"
        logger.info(f"正在搜索刺猬猫: {search_url}")

        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(search_url, timeout=10) as resp:
                    content = await resp.text()
                    tree = html.fromstring(content)
                    # 对应规则: class.rank-book-list@tag.li
                    book_nodes = tree.xpath("//div[contains(@class, 'rank-book-list')]/ul/li")
                    
                    results = []
                    for node in book_nodes:
                        name = node.xpath(".//p[contains(@class, 'tit')]/a/text()")
                        url = node.xpath(".//p[contains(@class, 'tit')]/a/@href")
                        author = node.xpath(".//div[contains(@class, 'cnt')]//p[contains(@class, 'author') or position()=2]/a/text()")
                        
                        if name and url:
                            results.append({
                                "name": name[0].strip(),
                                "author": author[0].strip() if author else "未知",
                                "url": url[0] if url[0].startswith("http") else self.base_url + url[0]
                            })

                    if return_metadata:
                        # 刺猬猫搜索页通常 10-20 条，判定是否为最后一页
                        is_last = len(results) < 10
                        return {"books": results, "total": 100, "current_page": page, "is_last": is_last}
                    return results
            except Exception as e:
                logger.error(f"刺猬猫搜索异常: {e}")
                return {"books": [], "total": 0, "current_page": page, "is_last": True} if return_metadata else []

    async def get_book_details(self, book_url):
        """解析刺猬猫详情页元数据"""
        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(book_url, timeout=10) as resp:
                    content = await resp.text()
                    tree = html.fromstring(content)
                    
                    # 1. 基础信息
                    name = tree.xpath("//div[contains(@class, 'book-info')]//p[contains(@class, 'tit')]/text()")
                    author = tree.xpath("//div[contains(@class, 'book-intro-cnt')]//span[contains(text(), '作者')]/following-sibling::a/text()")
                    cover = tree.xpath("//div[contains(@class, 'cover')]//img/@src")
                    
                    # 2. 状态与分类 (对应规则中的 kind)
                    # 刺猬猫通常在 span 中展示类别和状态
                    info_spans = tree.xpath("//div[contains(@class, 'book-intro-cnt')]//span/text()")
                    category = info_spans[0] if len(info_spans) > 0 else "刺猬猫小说"
                    status = "连载" if "连载" in str(info_spans) else "完结"
                    
                    # 3. 简介与更新 (对应规则 ruleBookInfo.intro)
                    update_time = tree.xpath("//span[contains(@class, 'update-time')]/text()")
                    desc = tree.xpath("//p[contains(@class, 'book-desc')]/text()")
                    
                    # 4. 数据字典 - 严格匹配现有类型，缺失则设为 None
                    return {
                        "name": name[0].strip() if name else "未知",
                        "author": author[0].strip() if author else "未知",
                        "intro": "　　" + "\n　　".join([i.strip() for i in desc if i.strip()]),
                        "cover": cover[0] if cover else None,
                        "status": status,
                        "word_count": None, # 刺猬猫详情页字数通常在 info_spans 中，格式不一，暂留空
                        "total_chapters": None,
                        "rank": None, # 刺猬猫规则中未提供月票排行属性
                        "category": category,
                        "tags": tree.xpath("//div[contains(@class, 'book-intro-cnt')]//span/text()")[1:4],
                        "rating": None,
                        "rating_users": None,
                        "collection": None,
                        "all_recommend": None,
                        "last_chapter": None,
                        "last_update": update_time[0].replace("最近更新：", "").strip() if update_time else None,
                        "first_chapter_title": None,
                        "first_chapter_content": None,
                        "url": book_url
                    }
            except Exception as e:
                logger.error(f"刺猬猫详情获取异常: {e}")
                return None