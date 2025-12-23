import re
from astrbot.api import logger

class MultiSearchEngine:
    @staticmethod
    def get_weight(priority_str: str):
        """将用户配置的优先级(1, 2)转换为权重系数"""
        if priority_str == "0": return 0.0
        if priority_str == "1": return 1.5  # 优先级越高，权重系数越大
        return 1.0

    @staticmethod
    def calculate_score(book, keyword, weight):
        """智能评分算法"""
        name = book.get('name', '')
        author = book.get('author', '未知')
        base_score = 0

        # 1. 匹配度打分
        if name == keyword:
            base_score = 100
        elif name.startswith(keyword):
            base_score = 85
        elif keyword in name:
            base_score = 70
        else:
            # 2. 动态去噪：计算关键词字符包含率
            match_chars = [c for c in keyword if c in name]
            if len(match_chars) >= len(keyword) * 0.5:
                base_score = 20  # 弱关联
            else:
                base_score = 0   # 噪音项，直接舍弃

        # 3. 作者匹配补偿
        if keyword in author:
            base_score += 15

        final_score = base_score * weight
        
        if final_score > 0:
            logger.info(f"[搜索评分] {book.get('origin')} | Score: {final_score:.1f} | 《{name}》")
        
        return final_score

    @classmethod
    def rank_results(cls, pool, keyword, qd_w, cwm_w):
        """对全平台池子进行动态竞标排序"""
        weights = {"qidian": qd_w, "ciweimao": cwm_w}
        ranked = []
        
        for book in pool:
            w = weights.get(book['origin'], 0)
            if w <= 0: continue
            
            score = cls.calculate_score(book, keyword, w)
            # 自动过滤：总分低于 40 的视为噪音不予展示
            if score >= 40:
                book['final_score'] = score
                ranked.append(book)
                
        return sorted(ranked, key=lambda x: x['final_score'], reverse=True)