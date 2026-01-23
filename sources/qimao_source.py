import aiohttp
import asyncio
import json
import hashlib
import base64
import time
import re
from datetime import datetime
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from .base_source import BaseSource

class QiMaoSource(BaseSource):
    BASE_URL = "https://api-bc.wtzw.com"
    SIGN_KEY = "d3dGiJc651gSQ8w1"
    DEFAULT_HEADERS = {
        'User-Agent': 'okhttp/3.12.12',
        'app-version': '51110',
        'platform': 'android',
        'reg': '0',
        'AUTHORIZATION': '',
        'application-id': 'com.kmxs.reader', 
        'net-env': '1',
        'channel': 'unknown',
        'qm-params': ''
    }

    def _get_md5(self, s):
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    def _sign_params(self, params):
        """Sign parameters using the source logic: md5(key1=val1key2=val2... + sign_key)"""
        # Exclude 'User-Agent' from signature
        sign_dict = {k: v for k, v in params.items() if k != 'User-Agent'}
        
        # 1. Sort keys
        sorted_keys = sorted(sign_dict.keys())
        # 2. Concat key=value
        raw_str = "".join([f"{k}={sign_dict[k]}" for k in sorted_keys])
        # 3. Add sign_key and md5
        return self._get_md5(raw_str + self.SIGN_KEY)

    def _aes_decrypt(self, encrypted_base64):
        """Decrypt content using AES/CBC/PKCS5Padding with fixed key/iv logic from source"""
        # Key: "242ccb8230d709e1"
        key = b"242ccb8230d709e1"
        
        try:
            iv_enc_data = base64.b64decode(encrypted_base64)
            iv = iv_enc_data[:16]
            ciphertext = iv_enc_data[16:]
            
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted = unpad(cipher.decrypt(ciphertext), AES.block_size)
            return decrypted.decode('utf-8')
        except Exception as e:
            # print(f"DEBUG: Decryption failed: {e}")
            return None

    async def search_book(self, keyword: str, page: int = 1, return_metadata: bool = False):
        url = f"{self.BASE_URL}/api/v5/search/words"
        
        headers = self.DEFAULT_HEADERS.copy()
        headers['sign'] = self._sign_params(headers)
        
        params = {
            'gender': '3', 
            'imei_ip': '2937357107',
            'page': str(page),
            'wd': keyword
        }
        params['sign'] = self._sign_params(params)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=10) as resp:
                    data = await resp.json()
                    
                    root_data = data.get('data', {})
                    books = root_data.get('books', [])
                    
                    results = []
                    for b in books:
                        # Extract tags
                        tags = []
                        if b.get('ptags'):
                            # ptags can be a list of dicts or strings, based on test file logic it seemed direct or we check structure
                            # Test file said: '分类': b.get('ptags')
                            # Let's assume it's a list of strings or list of objects with title.
                            # In `final_test_qimao.py`: '分类': b.get('ptags')
                            # If it is just a string/list, we use it.
                            ptags = b.get('ptags')
                            if isinstance(ptags, list):
                                tags = [str(t) for t in ptags]
                            elif isinstance(ptags, str):
                                tags = [ptags]
                        
                        book_info = {
                            'origin': 'qimao',
                            'name': b.get('original_title'),
                            'author': b.get('original_author'),
                            'cover': b.get('image_link'),
                            'intro': b.get('intro'),
                            'word_count': b.get('words_num'),
                            'tags': tags,
                            'rating': b.get('score'),
                            'book_id': b.get('id'),
                            'bid': b.get('id'),
                            'url': f"https://www.qimao.com/shuku/{b.get('id')}/"
                        }
                        results.append(book_info)

                    # Meta info for pagination
                    meta = root_data.get('meta', {})
                    total_page = int(meta.get('total_page', 0))
                    
                    if return_metadata:
                        return {
                            "books": results,
                            "max_pages": total_page if total_page > 0 else 1
                        }
                    return results

        except Exception as e:
            print(f"QiMao Search Error: {e}")
            if return_metadata:
                return {"books": [], "max_pages": 1}
            return []

    async def get_book_details(self, book_url: str):
        # Extract ID from URL
        # URL format: https://www.qimao.com/shuku/{id}/
        match = re.search(r'shuku/(\d+)', book_url)
        if not match:
            return None
        
        book_id = match.group(1)
        url = f"{self.BASE_URL}/api/v4/book/detail"
        
        headers = self.DEFAULT_HEADERS.copy()
        headers['sign'] = self._sign_params(headers)
        
        params = {
            'id': str(book_id),
            'imei_ip': '2937357107',
            'teeny_mode': '0'
        }
        params['sign'] = self._sign_params(params)
        
        # Prepare chapter list request for total chapters and trial
        chapter_url = "https://api-ks.wtzw.com/api/v1/chapter/chapter-list"
        c_headers = self.DEFAULT_HEADERS.copy()
        c_headers['sign'] = self._sign_params(c_headers)
        c_params = {'id': str(book_id)}
        c_params['sign'] = self._sign_params(c_params)

        try:
            async with aiohttp.ClientSession() as session:
                # Concurrent requests: Detail + Chapter List
                task_detail = session.get(url, headers=headers, params=params, timeout=10)
                task_chapters = session.get(chapter_url, headers=c_headers, params=c_params, timeout=5)
                
                resp_detail, resp_chapters = await asyncio.gather(task_detail, task_chapters, return_exceptions=True)
                
                # 1. Process Book Detail
                if isinstance(resp_detail, Exception) or resp_detail.status != 200:
                    return None
                
                data = await resp_detail.json()
                book_data = data.get('data', {}).get('book', {})
                if not book_data:
                    return None
                
                # Status and Category logic
                cat_over_words = str(book_data.get('category_over_words', ''))
                category = cat_over_words
                word_count = book_data.get('words_num')
                status = "完结" # Default

                parts = cat_over_words.split('・')
                if len(parts) >= 1:
                    category = parts[0]
                
                for p in parts:
                    if '万字' in p:
                        word_count = p
                    elif '完结' in p:
                        status = "完结"
                    elif '连载' in p:
                        status = "连载"
                
                update_status = str(book_data.get('update_status'))
                if update_status == "0":
                        status = "连载"

                # Timestamp formatting
                update_time = book_data.get('update_time')
                if update_time:
                    try:
                        ts = int(update_time)
                        update_time = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
                    except:
                        pass
                
                tags = [t['title'] for t in book_data.get('book_tag_list', [])]
                
                info = {
                    'origin': 'qimao',
                    'name': book_data.get('title'),
                    'author': book_data.get('author'),
                    'cover': book_data.get('image_link'),
                    'intro': book_data.get('intro'),
                    'word_count': word_count,
                    'status': status,
                    'last_update': update_time,
                    'last_chapter': book_data.get('latest_chapter_title'),
                    'tags': tags,
                    'category': category,
                    'rating': book_data.get('score'),
                    'url': f"https://www.qimao.com/shuku/{book_id}/",
                    'book_id': str(book_id),
                    'bid': str(book_id),
                    'copyright': book_data.get('statement')
                }
                
                # 2. Process Chapter List (Total Chapters & Trial Target)
                target_chap = None
                if not isinstance(resp_chapters, Exception) and resp_chapters.status == 200:
                    try:
                        c_data = await resp_chapters.json()
                        chapter_lists = c_data.get('data', {}).get('chapter_lists', [])
                        info['total_chapters'] = len(chapter_lists)
                        
                        # Find first free chapter
                        for ch in chapter_lists:
                            is_free = False
                            is_vip = ch.get('is_vip')
                            if is_vip is None or str(is_vip) == '0' or ch.get('price') == 0:
                                is_free = True
                            
                            if is_free:
                                target_chap = ch
                                break
                    except Exception as e:
                        print(f"QiMao Chapter List Parse Error: {e}")

                # 3. Fetch Trial Content (if target found)
                if target_chap:
                    content_url = "https://api-ks.wtzw.com/api/v1/chapter/content"
                    cc_params = {
                        'id': str(book_id),
                        'chapterId': str(target_chap['id'])
                    }
                    cc_params['sign'] = self._sign_params(cc_params)
                    
                    try:
                        async with session.get(content_url, headers=headers, params=cc_params, timeout=5) as c_resp:
                            if c_resp.status == 200:
                                c_data = await c_resp.json()
                                encrypted_content = c_data.get('data', {}).get('content')
                                if encrypted_content:
                                    content = self._aes_decrypt(encrypted_content)
                                    if content:
                                        info['first_chapter_title'] = target_chap.get('title')
                                        info['first_chapter_content'] = content
                    except Exception as e:
                         print(f"QiMao Content Error: {e}")

                return info

        except Exception as e:
            print(f"QiMao Details Error: {e}")
            return None

    async def _get_trial_content(self, book_id):
        """Fetch first chapter content for trial"""
        list_url = "https://api-ks.wtzw.com/api/v1/chapter/chapter-list"
        
        headers = self.DEFAULT_HEADERS.copy()
        headers['sign'] = self._sign_params(headers)
        
        params = {'id': str(book_id)}
        params['sign'] = self._sign_params(params)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(list_url, headers=headers, params=params, timeout=5) as resp:
                    data = await resp.json()
                    chapter_lists = data.get('data', {}).get('chapter_lists', [])
                    
                    target_chap = None
                    for ch in chapter_lists:
                        is_free = False
                        is_vip = ch.get('is_vip')
                        if is_vip is None or str(is_vip) == '0' or ch.get('price') == 0:
                            is_free = True
                        
                        if is_free:
                            target_chap = ch
                            break
                    
                    if not target_chap:
                        return None
                        
                    # Fetch content
                    content_url = "https://api-ks.wtzw.com/api/v1/chapter/content"
                    c_params = {
                        'id': str(book_id),
                        'chapterId': str(target_chap['id'])
                    }
                    c_params['sign'] = self._sign_params(c_params)
                    
                    async with session.get(content_url, headers=headers, params=c_params, timeout=5) as c_resp:
                        c_data = await c_resp.json()
                        encrypted_content = c_data.get('data', {}).get('content')
                        
                        if encrypted_content:
                            content = self._aes_decrypt(encrypted_content)
                            if content:
                                return {
                                    'first_chapter_title': target_chap.get('title'),
                                    'first_chapter_content': content
                                }
            return None
        except Exception as e:
            print(f"QiMao Trial Content Error: {e}")
            return None
