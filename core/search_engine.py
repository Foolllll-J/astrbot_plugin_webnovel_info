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
    def interleave_results(cls, good_books: list, qd_priority: str, cwm_priority: str):
        """按平台优先级交叉排列结果（高优先级先出）"""
        # 按得分降序分组
        qidian_books = sorted(
            [b for b in good_books if b.get('origin') == 'qidian'],
            key=lambda x: x['final_score'],
            reverse=True
        )
        ciweimao_books = sorted(
            [b for b in good_books if b.get('origin') == 'ciweimao'],
            key=lambda x: x['final_score'],
            reverse=True
        )
        
        # 确定优先级顺序
        high_prio_books = qidian_books if qd_priority <= cwm_priority else ciweimao_books
        low_prio_books = ciweimao_books if qd_priority <= cwm_priority else qidian_books
        
        # 交叉合并结果
        interleaved = []
        idx_high, idx_low = 0, 0
        while idx_high < len(high_prio_books) or idx_low < len(low_prio_books):
            if idx_high < len(high_prio_books):
                interleaved.append(high_prio_books[idx_high])
                idx_high += 1
            if idx_low < len(low_prio_books):
                interleaved.append(low_prio_books[idx_low])
                idx_low += 1
        
        return interleaved