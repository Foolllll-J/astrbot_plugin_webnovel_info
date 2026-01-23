import aiohttp
import re
import urllib.parse
from bs4 import BeautifulSoup
from astrbot.api import logger
from .base_source import BaseSource

class FalooSource(BaseSource):
    def __init__(self):
        self.base_url = "https://wap.faloo.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://wap.faloo.com/"
        }

    async def search_book(self, keyword, page=1, return_metadata=False):
        # 飞卢使用 GB18030 编码进行搜索
        try:
            encoded_key = urllib.parse.quote(keyword.encode('gb18030'))
        except Exception as e:
            logger.error(f"[飞卢] 关键词编码失败: {e}")
            return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []

        # 映射 Bot 页码到飞卢页码
        # Bot: 10/page
        # Faloo: 30/page
        # Bot Page 1 -> Faloo Page 1, Offset 0
        # Bot Page 2 -> Faloo Page 1, Offset 10
        # Bot Page 3 -> Faloo Page 1, Offset 20
        # Bot Page 4 -> Faloo Page 2, Offset 0
        
        bot_page = page
        faloo_page = (bot_page - 1) // 3 + 1
        offset_idx = ((bot_page - 1) % 3) * 10
        
        url = f"https://wap.faloo.com/search_1_{faloo_page}.html?k={encoded_key}"
        logger.info(f"[飞卢] 正在搜索: {url} (Bot Page {bot_page} -> Faloo Page {faloo_page}, Offset {offset_idx})")

        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        logger.error(f"[飞卢] 搜索请求失败: {resp.status}")
                        return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []
                    
                    # 读取二进制并解码
                    content_bytes = await resp.read()
                    content = content_bytes.decode('gb18030', errors='ignore')
                    
                    soup = BeautifulSoup(content, 'html.parser')
                    results = []
                    
                    # 解析列表
                    items = soup.select('.novelList li')
                    for item in items:
                        try:
                            name_tag = item.select_one('.bl_r1_tit a')
                            if not name_tag: continue
                            
                            author_tag = item.select_one('.nl_r1_author a')
                            cover_img = item.select_one('.nl_r1 a img')
                            intro_tag = item.select_one('.bl_r1_into a')
                            word_count_tag = item.select_one('.nl_r2 i')
                            
                            href = name_tag['href']
                            if href.startswith('//'):
                                book_url = "https:" + href
                            elif href.startswith('/'):
                                book_url = "https://wap.faloo.com" + href
                            else:
                                book_url = href
                                
                            results.append({
                                'name': name_tag.get_text(strip=True),
                                'author': author_tag.get_text(strip=True) if author_tag else "未知",
                                'url': book_url,
                                'origin': 'faloo',
                                'cover': cover_img.get('src') if cover_img else None,
                                'intro': intro_tag.get_text(strip=True) if intro_tag else None,
                                'word_count': word_count_tag.get_text(strip=True) if word_count_tag else None,
                                'bid': re.search(r'(\d+)\.html', book_url).group(1) if re.search(r'(\d+)\.html', book_url) else None
                            })
                        except Exception as e:
                            continue
                            
                    # 飞卢固定每页 30 条，如果少于 30 条说明是最后一页
                    page_size_faloo = 30
                    current_count = len(results)
                    is_faloo_page_last = current_count < page_size_faloo
                    
                    # 切片获取当前 Bot 页的数据
                    sliced_results = results[offset_idx : offset_idx + 10]
                    
                    # 判断是否为最后一页
                    if is_faloo_page_last:
                         # 如果飞卢页是最后一页，检查当前切片是否已包含剩下的所有数据
                         is_last = (offset_idx + 10) >= current_count
                    else:
                         # 如果飞卢页不是最后一页，说明还有下一页飞卢数据
                         is_last = False
                    
                    # 处理空切片的情况 (例如请求了 Bot Page 4 但 Faloo Page 2 其实是空的)
                    if not sliced_results and offset_idx >= current_count:
                         is_last = True
                    
                    if return_metadata:
                        return {
                            "books": sliced_results,
                            "total": 9999, # 无法获取精确总数
                            "max_pages": 999, # 无法获取精确最大页数
                            "current_page": bot_page,
                            "is_last": is_last
                        }
                    
                    return sliced_results

            except Exception as e:
                logger.error(f"[飞卢] 搜索异常: {e}")
                return {"books": [], "total": 0, "max_pages": 1, "is_last": True} if return_metadata else []

    async def get_book_details(self, book_url):
        async with aiohttp.ClientSession(headers=self.headers) as session:
            try:
                async with session.get(book_url, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    
                    content_bytes = await resp.read()
                    content = content_bytes.decode('gb18030', errors='ignore')
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    book_info = {
                        "url": book_url,
                        "origin": "faloo"
                    }
                    
                    # Extract BID
                    match_bid = re.search(r'(\d+)\.html', book_url)
                    if match_bid:
                        book_info['bid'] = match_bid.group(1)
                    
                    # 1. 基本信息
                    name_tag = soup.select_one('.name')
                    if name_tag: book_info['name'] = name_tag.get_text(strip=True)
                    
                    author_links = soup.select('.color999 a')
                    if author_links:
                        book_info['author'] = author_links[0].get_text(strip=True)
                        if len(author_links) > 1:
                            book_info['category'] = author_links[1].get_text(strip=True)
                            
                    status_tag = soup.select_one('.color999 .tag.textHide')
                    if status_tag: book_info['status'] = status_tag.get_text(strip=True)
                    
                    tags = [a.get_text(strip=True) for a in soup.select('.tagList a')]
                    if tags: 
                        # Deduplicate tags while preserving order
                        book_info['tags'] = list(dict.fromkeys(tags))
                    
                    cover_img = soup.select_one('.cover_box img')
                    if cover_img: book_info['cover'] = cover_img.get('src')
                    
                    intro_p = soup.select_one('#novel_intro')
                    if intro_p: book_info['intro'] = intro_p.get_text('\n', strip=True)
                    
                    last_chap = soup.select_one('.newNode')
                    if last_chap: book_info['last_chapter'] = last_chap.get_text(strip=True)
                    
                    # Total Chapters
                    count_text = soup.select_one('.countText')
                    if count_text:
                        # Extract digits from "本书已更592章"
                        raw_count = count_text.get_text(strip=True)
                        match = re.search(r'(\d+)', raw_count)
                        if match:
                            book_info['total_chapters'] = match.group(1)
                        else:
                            book_info['total_chapters'] = raw_count

                    # 2. 统计信息
                    info_ul = soup.select_one('ul.info')
                    if info_ul:
                        lis = info_ul.find_all('li')
                        for li in lis:
                            text = li.get_text(strip=True)
                            if '万字' in text:
                                parts = text.split('|')
                                if len(parts) >= 1: book_info['word_count'] = parts[0].strip()
                                if len(parts) >= 2: book_info['total_click'] = parts[1].strip()
                            if '更新时间：' in text:
                                book_info['last_update'] = text.replace('更新时间：', '').strip()
                            if '分' in text and '已评' in text:
                                # "9.4分 / 1912人已评"
                                raw_score = text.strip()
                                try:
                                    score_parts = raw_score.split('/')
                                    if len(score_parts) >= 1:
                                        book_info['rating'] = score_parts[0].replace('分', '').strip()
                                    if len(score_parts) >= 2:
                                        book_info['rating_users'] = score_parts[1].replace('人已评', '').strip()
                                except:
                                    book_info['rating'] = raw_score

                    # Reward Stats (Flowers, Tickets, etc.)
                    rewards = soup.select('.reward li')
                    if len(rewards) >= 4:
                        try:
                            book_info['reward_coin'] = rewards[0].select_one('span').get_text(strip=True)
                            book_info['reward_flower'] = rewards[1].select_one('span').get_text(strip=True)
                            book_info['reward_ticket'] = rewards[2].select_one('span').get_text(strip=True)
                            book_info['reward_review'] = rewards[3].select_one('span').get_text(strip=True)
                        except:
                            pass

                    # 3. 获取试读内容（第一章）
                    nav_links = soup.select('.display_flex_between a')
                    if len(nav_links) > 1:
                        href = nav_links[1]['href']
                        if href.startswith('//'):
                            catalog_url = "https:" + href
                        elif href.startswith('/'):
                            catalog_url = "https://wap.faloo.com" + href
                        else:
                            catalog_url = href
                        
                        # 请求目录页
                        async with session.get(catalog_url, timeout=5) as c_resp:
                            if c_resp.status == 200:
                                c_bytes = await c_resp.read()
                                c_content = c_bytes.decode('gb18030', errors='ignore')
                                c_soup = BeautifulSoup(c_content, 'html.parser')
                                
                                # 查找免费章节
                                chapter_url = None
                                chapters = c_soup.select('.v_nodeList li a')
                                for link in chapters:
                                    # 排除 VIP 章节 (通常有 icon_close 或 vip 图标)
                                    if link.select('.icon_close') or link.select('img[src*="vip"]'):
                                        continue
                                    
                                    c_href = link.get('href')
                                    if c_href:
                                        if c_href.startswith('//'):
                                            chapter_url = "https:" + c_href
                                        elif c_href.startswith('/'):
                                            chapter_url = "https://wap.faloo.com" + c_href
                                        else:
                                            chapter_url = c_href
                                        break
                                
                                if chapter_url:
                                    async with session.get(chapter_url, timeout=5) as ch_resp:
                                        if ch_resp.status == 200:
                                            ch_bytes = await ch_resp.read()
                                            ch_content = ch_bytes.decode('gb18030', errors='ignore')
                                            ch_soup = BeautifulSoup(ch_content, 'html.parser')
                                            
                                            title = ch_soup.select_one('h1') or ch_soup.select_one('.title')
                                            if title: book_info['first_chapter_title'] = title.get_text(strip=True)
                                            
                                            content_div = ch_soup.select_one('.nodeContent')
                                            if content_div:
                                                ps = content_div.find_all('p')
                                                if ps:
                                                    lines = [p.get_text(strip=True) for p in ps]
                                                    book_info['first_chapter_content'] = "\n".join(lines)
                                                else:
                                                    book_info['first_chapter_content'] = content_div.get_text('\n', strip=True)

                    return book_info

            except Exception as e:
                logger.error(f"[飞卢] 详情获取异常: {e}")
                return None
