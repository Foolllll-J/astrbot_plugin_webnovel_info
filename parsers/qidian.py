import asyncio
import aiohttp
import re
from urllib.parse import urljoin

class QidianParser:
    def __init__(self):
        self.base_url = "https://www.qidian.com"
        self.search_url = "https://www.qidian.com/so/{}.html"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
        }

    async def search_book(self, book_name: str) -> list:
        """
        Searches for a book on qidian.com and returns a list of results.
        """
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(self.search_url.format(book_name), headers=self.headers) as response:
                    response.raise_for_status()
                    html_content = await response.text()

                # Using regex based on bookSource.json's ruleSearch
                # bookList: class.book-img-text@tag.li
                book_list_pattern = re.compile(r'<li data-rid.*?>(.*?)</li>', re.DOTALL)
                
                # name: class.book-info-title@text
                name_pattern = re.compile(r'<a.*?class="book-info-title".*?>(.*?)</a>', re.DOTALL)
                
                # bookUrl: class.btn@tag.a@href
                url_pattern = re.compile(r'<a.*?class="book-info-title".*?href="(.*?)".*?>', re.DOTALL)
                
                # author: class.author@tag.a.0@text
                author_pattern = re.compile(r'<p class="author">.*?<a.*?>(.*?)</a>.*?</p>', re.DOTALL)
                
                # intro: class.intro@textNodes
                intro_pattern = re.compile(r'<p class="intro">(.*?)</p>', re.DOTALL)

                books = []
                for item_html in book_list_pattern.findall(html_content):
                    name_match = name_pattern.search(item_html)
                    url_match = url_pattern.search(item_html)
                    author_match = author_pattern.search(item_html)
                    intro_match = intro_pattern.search(item_html)

                    if name_match and url_match and author_match:
                        books.append({
                            "name": name_match.group(1).strip(),
                            "url": urljoin(self.base_url, url_match.group(1)),
                            "author": author_match.group(1).strip(),
                            "intro": intro_match.group(1).strip() if intro_match else ""
                        })
                return books
            except Exception as e:
                print(f"Error during search: {e}")
                return []

    async def get_book_details(self, book_url: str) -> dict:
        """
        Gets book details from a qidian book page.
        """
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(book_url, headers=self.headers) as response:
                    response.raise_for_status()
                    html_content = await response.text()

                # Based on ruleBookInfo
                # name: id.bookName@text -> <h1 id="bookName">...</h1>
                name_match = re.search(r'<h1 id="bookName">(.*?)</h1>', html_content)
                name = name_match.group(1).strip() if name_match else None

                # author: id.bookName@text (This seems wrong in bookSource, let's find the author)
                # Let's try to find it by class
                author_match = re.search(r'<a class="writer".*?>(.*?)</a>', html_content)
                author = author_match.group(1).strip() if author_match else "未知"

                # coverUrl: id.bookImg@tag.img@src
                cover_match = re.search(r'<div id="bookImg".*?<img src="(.*?)".*?>', html_content, re.DOTALL)
                cover_url = urljoin(self.base_url, cover_match.group(1)) if cover_match else None

                # intro: id.book-intro-detail@html
                intro_match = re.search(r'<div id="book-intro-detail">(.*?)</div>', html_content, re.DOTALL)
                intro = re.sub('<.*?>', '', intro_match.group(1)).strip() if intro_match else "无简介"

                # kind: various selectors, let's get tags
                tags_match = re.findall(r'<p class="tag">.*?</p>', html_content, re.DOTALL)
                tags = []
                if tags_match:
                    tag_links = re.findall(r'<a.*?>(.*?)</a>', tags_match[0])
                    tags = [tag.strip() for tag in tag_links]

                # lastChapter: class.latest-chapter@text
                last_chapter_match = re.search(r'<p class="cf">.*?<a.*?class="blue".*?>(.*?)</a>.*?</p>', html_content, re.DOTALL)
                last_chapter = last_chapter_match.group(1).strip() if last_chapter_match else "无最新章节"


                if not name:
                    return None

                return {
                    "name": name,
                    "author": author,
                    "cover_url": cover_url,
                    "intro": intro,
                    "tags": tags,
                    "last_chapter": last_chapter,
                    "url": book_url
                }
            except Exception as e:
                print(f"Error getting details: {e}")
                return None
