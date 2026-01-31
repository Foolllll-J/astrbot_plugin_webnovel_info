
import aiohttp
import re
from bs4 import BeautifulSoup
from urllib.parse import quote
from astrbot.api import logger
from .base_source import BaseSource

class SfacgSource(BaseSource):
    def __init__(self):
        self.base_url = "https://book.sfacg.com"
        self.search_url = "http://s.sfacg.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://book.sfacg.com/"
        }

    async def search_book(self, keyword, page=1, return_metadata=False):
        encoded_key = quote(keyword)
        url = f"{self.search_url}/?Key={encoded_key}&S=1&SS=0"
        
        logger.info(f"[菠萝包] 正在搜索: {url}")

        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        logger.error(f"[菠萝包] 搜索请求失败: {resp.status}")
                        return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []
                        
                    content = await resp.text(encoding='utf-8')
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    all_results = []
                    items = soup.select('table.comic_cover ul')
                    
                    for item in items:
                        name_tag = item.select_one('strong a')
                        if name_tag:
                            href = name_tag['href']
                            full_url = "https://book.sfacg.com" + href if href.startswith('/') else href
                            
                            book_id = None
                            match = re.search(r'/Novel/(\d+)', full_url)
                            if match: book_id = match.group(1)
                            
                            author = "未知"
                            text_content = item.get_text()
                            info_match = re.search(r'综合信息：\s*(.*?)/', text_content)
                            if info_match:
                                author = info_match.group(1).strip()
                            
                            cover_img = item.select_one('img')
                            cover_url = cover_img.get('src') if cover_img else None
                            
                            all_results.append({
                                "name": name_tag.get_text(strip=True),
                                "author": author,
                                "url": full_url,
                                "origin": "sfacg",
                                "cover": cover_url,
                                "bid": book_id,
                                "book_id": book_id
                            })
                    
                    total_count = len(all_results)
                    
                    page_size = 10
                    max_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
                    
                    if page < 1: page = 1
                    if page > max_pages: page = max_pages
                    
                    start_idx = (page - 1) * page_size
                    end_idx = start_idx + page_size
                    page_results = all_results[start_idx:end_idx]
                    
                    if return_metadata:
                        return {
                            "books": page_results,
                            "total": total_count,
                            "max_pages": max_pages,
                            "current_page": page,
                            "is_last": page >= max_pages
                        }
                    
                    return page_results

            except Exception as e:
                logger.error(f"[菠萝包] 搜索异常: {e}")
                return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []

    async def get_book_details(self, book_url):
        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(book_url, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    content = await resp.text()
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    book_info = {
                        "url": book_url,
                        "origin": "sfacg"
                    }
                    
                    # 1. Title
                    title_tag = soup.select_one('.d-summary .title .text')
                    if title_tag:
                        for tag in title_tag.find_all(True):
                            tag.decompose()
                        book_info['name'] = title_tag.get_text(strip=True)
                    
                    # 2. Author
                    author_tag = soup.select_one('.author-name span')
                    if author_tag:
                        book_info['author'] = author_tag.get_text(strip=True)
                        
                    # 3. Cover
                    cover_img = soup.select_one('.books-box .pic img')
                    if cover_img:
                        book_info['cover'] = cover_img.get('src')
                        
                    # 4. Intro
                    intro_tag = soup.select_one('.introduce')
                    if intro_tag:
                        book_info['intro'] = intro_tag.get_text(strip=True)
                        
                    # 5. Tags
                    tags = [t.get_text(strip=True) for t in soup.select('.tag-list .tag .text')]
                    book_info['tags'] = tags
                    
                    # 6. Metadata
                    count_details = soup.select('.count-detail .text')
                    for span in count_details:
                        text = span.get_text(strip=True)
                        if '字数' in text:
                            raw_wc = text.replace('字数：', '')
                            m = re.search(r'(.*?)\[(.*?)\]', raw_wc)
                            if m:
                                book_info['word_count'] = m.group(1)
                                book_info['status'] = m.group(2)
                            else:
                                book_info['word_count'] = raw_wc
                        if '类型' in text:
                            book_info['category'] = text.replace('类型：', '')
                        if '点击' in text:
                            book_info['total_click'] = text.replace('点击：', '')
                        if '更新' in text:
                            book_info['last_update'] = text.replace('更新：', '').strip()
                            
                    # 7. Latest Chapter
                    last_chapter_tag = soup.select_one('.chapter-title .link')
                    if last_chapter_tag:
                        book_info['last_chapter'] = last_chapter_tag.get_text(strip=True)
                    
                    # 8. Trial Content (First Chapter)
                    try:
                        catalog_url = book_url.rstrip('/') + "/MainIndex/"
                        async with session.get(catalog_url, timeout=5) as c_resp:
                            if c_resp.status == 200:
                                c_soup = BeautifulSoup(await c_resp.text(), 'html.parser')
                                first_chap_link = None
                                for a in c_soup.select('.catalog-list li a'):
                                    href = a.get('href')
                                    if href and '/c/' in href and '/vip/' not in href:
                                        first_chap_link = href
                                        break
                                if not first_chap_link:
                                    first_chap = c_soup.select_one('.catalog-list li a')
                                    if first_chap:
                                        first_chap_link = first_chap.get('href')
                                
                                if first_chap_link:
                                    full_chap_url = "https://book.sfacg.com" + first_chap_link
                                    async with session.get(full_chap_url, timeout=5) as chap_resp:
                                        if chap_resp.status == 200:
                                            chap_soup = BeautifulSoup(await chap_resp.text(), 'html.parser')
                                            
                                            title_tag = chap_soup.select_one('.article-title')
                                            if title_tag:
                                                book_info['first_chapter_title'] = title_tag.get_text(strip=True)
                                            
                                            body = chap_soup.select_one('#ChapterBody')
                                            if body:
                                                paragraphs = body.find_all('p')
                                                if paragraphs:
                                                    book_info['first_chapter_content'] = "\n".join([p.get_text(strip=True) for p in paragraphs])
                                                else:
                                                    book_info['first_chapter_content'] = body.get_text(strip=True)
                    except Exception as e:
                        logger.warning(f"[菠萝包] 试读获取失败: {e}")
                    
                    return book_info

            except Exception as e:
                logger.error(f"[菠萝包] 详情获取异常: {e}")
                return None
