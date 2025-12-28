import re
from astrbot.api import logger

class MultiSearchEngine:
    @staticmethod
    def get_weight(priority_str: str) -> float:
        """根据优先级配置返回权重值"""
        # 缩小权重差距，避免权重直接反超匹配度
        priority_map = {"0": 0.0, "1": 1.1, "2": 1.0, "3": 0.9}
        return priority_map.get(priority_str, 1.0)

    @staticmethod
    def calculate_score(book: dict, keyword: str, weight: float) -> float:
        """基于书名/作者匹配度+平台权重的智能打分"""
        name = book.get('name', '')
        author = book.get('author', '未知')
        
        # 1. 书名匹配得分 (最高 100)
        if name == keyword:
            name_score = 100
        elif name.startswith(keyword):
            name_score = 80
        elif keyword in name:
            # 根据关键词在书名中的占比打分，避免长书名蹭热度
            # 基础分 50 + 占比分 20
            ratio = len(keyword) / len(name)
            name_score = 50 + (ratio * 20)
        else:
            # 模糊匹配：计算关键词字符在书名中的出现率
            match_chars = [c for c in keyword if c in name]
            match_ratio = len(match_chars) / len(keyword) if keyword else 0
            # 降低模糊匹配门槛，确保相关书籍能进入结果池
            name_score = 30 if match_ratio >= 0.4 else 0 
        
        # 2. 作者匹配得分 (最高 40)
        # 搜书时作者匹配仅作为辅助参考，分值大幅下调
        author_score = 40 if author == keyword else (20 if keyword in author else 0)
        
        # 3. 基础得分（取最高项）
        base_score = max(name_score, author_score)
        
        # 4. 最终得分（权重加权）
        final_score = base_score * weight
        
        # 最终打分结果
        logger.debug(f"[打分] {book.get('origin', 'unknown')} | 《{name}》 | 得分: {final_score:.1f} (权重: {weight})")
        
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
        logger.debug(f"[筛选] 有效书籍数: {len(valid_books)} | 平均分阈值: {avg_score:.2f}")
        
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
    def interleave_results(cls, good_books: list, qd_priority: str, tm_priority: str, cwm_priority: str):
        """按得分降序排列结果"""
        sorted_results = sorted(good_books, key=lambda x: x.get('final_score', 0), reverse=True)
        
        # 输出排序后的结果摘要
        if sorted_results:
            log_msg = "[排序] 最终排列顺序:\n"
            for i, b in enumerate(sorted_results[:10]): # 仅列出前10条
                log_msg += f"  {i+1}. 《{b['name']}》({b['origin']}) - 得分: {b['final_score']:.1f}\n"
            if len(sorted_results) > 10:
                log_msg += f"  ... 共 {len(sorted_results)} 条结果"
            logger.debug(log_msg)
            
        return sorted_results