import json
import os
from typing import List, Dict, Optional

class BookshelfManager:
    def __init__(self, data_dir: str):
        self.data_path = os.path.join(data_dir, "bookshelf.json")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        self.bookshelves = self._load_data()

    def _load_data(self) -> Dict[str, List[Dict]]:
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_data(self):
        try:
            with open(self.data_path, "w", encoding="utf-8") as f:
                json.dump(self.bookshelves, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存书架数据失败: {e}")

    def add_book(self, user_id: str, book: Dict) -> bool:
        """添加书籍到书架"""
        if user_id not in self.bookshelves:
            self.bookshelves[user_id] = []
        
        # 检查是否已存在 (根据 bid 和 origin 判断)
        for b in self.bookshelves[user_id]:
            if str(b.get("bid")) == str(book.get("bid")) and b.get("origin") == book.get("origin"):
                return False
        
        self.bookshelves[user_id].append({
            "name": book.get("name"),
            "author": book.get("author"),
            "origin": book.get("origin"),
            "bid": book.get("bid"),
            "url": book.get("url")
        })
        self._save_data()
        return True

    def remove_book(self, user_id: str, index: int) -> Optional[Dict]:
        """从书架移除书籍 (1-based index)"""
        if user_id not in self.bookshelves or not self.bookshelves[user_id]:
            return None
        
        if 1 <= index <= len(self.bookshelves[user_id]):
            removed = self.bookshelves[user_id].pop(index - 1)
            self._save_data()
            return removed
        return None

    def remove_book_by_info(self, user_id: str, bid: str, origin: str) -> bool:
        """根据书籍信息从书架移除"""
        if user_id not in self.bookshelves:
            return False
        
        initial_len = len(self.bookshelves[user_id])
        self.bookshelves[user_id] = [
            b for b in self.bookshelves[user_id] 
            if not (str(b.get("bid")) == str(bid) and b.get("origin") == origin)
        ]
        
        if len(self.bookshelves[user_id]) < initial_len:
            self._save_data()
            return True
        return False

    def get_bookshelf(self, user_id: str) -> List[Dict]:
        """获取用户书架内容"""
        return self.bookshelves.get(user_id, [])

    def get_book_by_index(self, user_id: str, index: int) -> Optional[Dict]:
        """通过序号获取书籍 (1-based index)"""
        books = self.get_bookshelf(user_id)
        if 1 <= index <= len(books):
            return books[index - 1]
        return None
