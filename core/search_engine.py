import re
from astrbot.api import logger

class MultiSearchEngine:
    @staticmethod
    def get_weight(priority_str: str) -> float:
        """根据优先级配置返回权重值"""
        priority_map = {"0": 0.0, "1": 1.5, "2": 1.0}
        return priority_map.get(priority_str, 1.0)

    @staticmethod
    def calculate_score(book: dict, keyword: str, weight: float) -> float:
        """基于书名/作者匹配度+平台权重的智能打分"""
        name = book.get('name', '')
        author = book.get('author', '未知')
        
        # 书名匹配得分
        if name == keyword:
            name_score = 100
        elif name.startswith(keyword):
            name_score = 85
        elif keyword in name:
            name_score = 70
        else:
            match_ratio = len([c for c in keyword if c in name]) / len(keyword) if keyword else 0
            name_score = 30 if match_ratio >= 0.5 else 0
        
        # 作者匹配得分
        author_score = 90 if author == keyword else (65 if keyword in author else 0)
        
        # 基础得分（取最高项，双高额外加分）
        base_score = max(name_score, author_score)
        if name_score >= 70 and author_score >= 65:
            base_score += 20
        
        # 最终得分（权重加权）
        final_score = base_score * weight
        
        # 仅DEBUG级别输出打分日志，降低生产环境日志量
        if final_score > 0:
            logger.debug(f"[打分] {book.get('origin', 'unknown')} | 《{name}》 | 得分: {final_score:.1f}")
        
        return final_score

    @classmethod
    def sift_by_average(cls, raw_batch: list, keyword: str, weights_map: dict):
        """按有效书籍平均分筛选高质量结果"""
        if not raw_batch:
            return [], [], 0.0
        
        # 计算所有书籍得分并过滤无效结果
        valid_books = []
        total_score = 0.0
        for book in raw_batch:
            platform_weight = weights_map.get(book.get('origin'), 1.0)
            score = cls.calculate_score(book, keyword, platform_weight)
            book['final_score'] = score
            if score > 0:
                valid_books.append(book)
                total_score += score
        
        if not valid_books:
            return [], [], 0.0
        
        # 计算有效书籍平均分
        avg_score = total_score / len(valid_books)
        logger.debug(f"[筛选] 有效书籍数: {len(valid_books)} | 平均分: {avg_score:.2f}")
        
        # 按平均分筛选结果
        sifted_books = []
        remaining_books = []
        for book in valid_books:
            if book['final_score'] >= avg_score:
                sifted_books.append(book)
            else:
                remaining_books.append(book)
        
        return sifted_books, remaining_books, avg_score

    @classmethod
    def interleave_results(cls, good_books: list, platforms_prio: list):
        """按平台优先级交叉排列结果（支持多平台扩展）
        
        Args:
            good_books: 待排列的书籍列表
            platforms_prio: 平台优先级列表，格式为 [(origin, priority_str), ...]
        """
        # 1. 按平台分组并按得分降序排序
        grouped_books = {}
        for origin, _ in platforms_prio:
            grouped_books[origin] = sorted(
                [b for b in good_books if b.get('origin') == origin],
                key=lambda x: x['final_score'],
                reverse=True
            )
        
        # 2. 过滤禁用平台并按优先级数字排序 (1 < 2)
        sorted_platforms = sorted(
            [p for p in platforms_prio if p[1] != "0"], 
            key=lambda x: int(x[1])
        )
        
        # 3. 交叉合并结果
        interleaved = []
        indices = {origin: 0 for origin, _ in sorted_platforms}
        
        while any(indices[origin] < len(grouped_books[origin]) for origin, _ in sorted_platforms):
            for origin, _ in sorted_platforms:
                if indices[origin] < len(grouped_books[origin]):
                    interleaved.append(grouped_books[origin][indices[origin]])
                    indices[origin] += 1
                    
        return interleaved