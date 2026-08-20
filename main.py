import asyncio
import aiohttp
import base64
import re
import os
from yarl import URL
from cachetools import TTLCache
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
import astrbot.api.message_components as Comp

from .sources import SourceManager
from .core.search_engine import MultiSearchEngine
from .core.bookshelf_manager import BookshelfManager

@register("astrbot_plugin_webnovel_info", "Foolllll", "网文搜索助手", "1.1.1", "")
class WebnovelInfoPlugin(Star):
    """网文搜索插件核心类
    支持多平台书籍搜索、分页、详情查看、试读内容展示
    """
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.source_manager = SourceManager()  # 数据源管理器
        self.bookshelf_manager = BookshelfManager(StarTools.get_data_dir("astrbot_plugin_webnovel_info"))
        self.config = config or {}             # 插件配置（默认空字典）
        
        # 显示模式：简洁/详细（默认详细）
        self.display_mode = "concise" if self.config.get("display_mode", "详细") == "简洁" else "detailed"
        self.enable_trial = self.config.get("enable_trial", False)  # 是否启用试读功能
        self.priority_cfg = self.config.get("platform_weights", "1 2 2").split()  # 平台权重配置
        
        # 初始化番茄 API 配置
        if "tomato" in self.source_manager.sources:
            self.source_manager.get_source("tomato").api_base = self.config.get("tomato_api_base", [])

        self.user_search_state = TTLCache(maxsize=1000, ttl=3600)
        
        self.trial_content_limit = 3000  # 试读内容长度限制（字符数）
        self.page_size = 10  
        self._session = None # 持久化会话

    async def get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
        return self._session

    def _get_user_search_state(self, user_id: str):
        """获取/初始化用户搜索状态
        
        Args:
            user_id: 用户唯一标识
        
        Returns:
            dict: 用户搜索状态字典，包含关键词、页码、缓存等信息
        """
        if user_id not in self.user_search_state:
            self.user_search_state[user_id] = {
                "keyword": "",          # 搜索关键词
                "current_page": 1,      # 当前页码
                "full_pool": [],        # 多平台综合结果池
                "raw_pool": [],         # 原始搜索结果池
                "qd_page": 1,           # 起点搜索页码
                "cwm_page": 1,          # 刺猬猫搜索页码
                "tm_page": 1,           # 番茄搜索页码
                "qd_last": False,       # 起点是否最后一页
                "cwm_last": False,      # 刺猬猫是否最后一页
                "tm_last": False,       # 番茄是否最后一页
                "source": "",           # 当前搜索源（multi/qidian/ciweimao/tomato）
                "max_pages": 1,         # 总页数
                "results": [],          # 当前页结果
                "single_pool": [],      # 单平台结果池
                "cached_pages": {},     # 页码缓存（key:页码，value:该页数据）
                "last_viewed": None,    # 最近查看的书籍信息
                "bookshelf_page": 1     # 书架当前页码
            }
        return self.user_search_state[user_id]

    # @filter.command("搜书", alias={'ss'})
    # async def multi_search_handler(self, event: AstrMessageEvent):
    async def _multi_search_handler_disabled(self, event: AstrMessageEvent):
        """多平台聚合搜索"""
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法: /ss <书名> 或 /ss <序号> 或 /ss n")
            return

        # 解析用户ID和操作指令
        user_id, action = event.get_sender_id(), parts[1]
        state = self._get_user_search_state(user_id)
        avg_threshold = 60  # 结果筛选阈值
        direct_index = None

        # 1. 序号查询：查看指定书籍详情 (e.g. /ss 1)
        if action.isdigit() and len(parts) == 2:
            idx = int(action) - 1
            if 0 <= idx < len(state["full_pool"]):
                target = state["full_pool"][idx]
                state["last_viewed"] = target # 记录最近查看
                details = await self.source_manager.get_source(target['origin']).get_book_details(target["url"])
                if details:
                    yield event.chain_result(await self._format_book_details(details))
                return
            yield event.plain_result(f"🤔 序号 {action} 不在当前结果中。")
            return

        # 2. 翻页操作 (e.g. /ss 下一页)
        if action in ("下一页", "下页", "n", "next", "上一页", "上页", "p", "prev") and len(parts) == 2:
            if not state["keyword"]:
                yield event.plain_result("❌ 请先搜索。")
                return
            req_page = state["current_page"] + (1 if action in ("下一页", "下页", "n", "next") else -1)
            if req_page < 1:
                yield event.plain_result("⬅️ 已经是第一页。")
                return
            keyword = state["keyword"]
        # 3. 新关键词搜索 或 直接查看详情 (e.g. /ss 诡秘之主 或 /ss 诡秘之主 1)
        else:
            # 解析直接查看详情索引
            if len(parts) >= 3 and parts[-1].isdigit():
                try:
                    direct_index = int(parts[-1])
                    keyword = " ".join(parts[1:-1])
                except ValueError:
                    keyword = " ".join(parts[1:])
            else:
                keyword = " ".join(parts[1:])
            
            if not keyword:
                yield event.plain_result("❌ 请输入关键词，例如：`/ss 诡秘之主`")
                return
            
            req_page = 1
            if state["keyword"] != keyword:
                yield event.plain_result(f"🔍 正在多平台搜索“{keyword}”...")
                # 重置搜索状态
                state.update({
                    "keyword": keyword, "full_pool": [], "raw_pool": [], 
                    "qd_page": 1, "cwm_page": 1, "tm_page": 1,
                    "qd_last": False, "cwm_last": False, "tm_last": False,
                    "source": "multi",
                    "cached_pages": {},
                    "last_viewed": None
                })

        # 计算目标页数需要的结果总数
        if direct_index:
            target_count = direct_index
        else:
            target_count = req_page * self.page_size
        qd_prio = self.priority_cfg[0] if len(self.priority_cfg) > 0 else "1"
        tm_prio = self.priority_cfg[1] if len(self.priority_cfg) > 1 else "2"
        cwm_prio = self.priority_cfg[2] if len(self.priority_cfg) > 2 else "2"
        
        weights_map = {
            "qidian": MultiSearchEngine.get_weight(qd_prio), 
            "tomato": MultiSearchEngine.get_weight(tm_prio),
            "ciweimao": MultiSearchEngine.get_weight(cwm_prio)
        }

        # 补充结果池直到满足目标页数需求
        avg_threshold = 60  # 结果筛选阈值
        max_batches = 5     # 最大拉取批次，防止低质量结果导致无限拉取
        batch_count = 0
        
        while len(state["full_pool"]) < target_count and batch_count < max_batches:
            batch_count += 1
            _, _, current_avg = MultiSearchEngine.sift_by_average(state["raw_pool"], keyword, weights_map)
            
            # 结果不足或质量不达标时，拉取更多数据
            # 如果所有平台都已拉完，或者当前已经有足够多的原始结果但质量仍不达标，则停止拉取
            all_exhausted = state["qd_last"] and state["cwm_last"] and state["tm_last"]
            need_more = not state["raw_pool"] or (current_avg < avg_threshold and not all_exhausted)
            
            if need_more:
                tasks, p_map = [], []
                # 起点搜索任务
                if qd_prio != "0" and not state["qd_last"]:
                    tasks.append(self.source_manager.get_source("qidian").search_book(keyword, page=state["qd_page"], return_metadata=True))
                    p_map.append("qidian")
                # 刺猬猫搜索任务
                if cwm_prio != "0" and not state["cwm_last"]:
                    tasks.append(self.source_manager.get_source("ciweimao").search_book(keyword, page=state["cwm_page"], return_metadata=True))
                    p_map.append("ciweimao")
                # 番茄搜索任务
                if tm_prio != "0" and not state["tm_last"] and self.config.get("tomato_api_base"):
                    tasks.append(self.source_manager.get_source("tomato").search_book(keyword, page=state["tm_page"], return_metadata=True))
                    p_map.append("tomato")
                
                if tasks:
                    # 并发执行搜索任务
                    logger.debug(f"[聚合搜索] 正在执行第 {batch_count} 批次拉取, 关键词: {keyword}")
                    results = await asyncio.gather(*tasks)
                    for i, r in enumerate(results):
                        if not r: continue
                        books = r.get('books', [])
                        platform = p_map[i]
                        if platform == "qidian":
                            state["qd_page"] += 1
                            state["qd_last"] = r.get('is_last', False)
                        elif platform == "ciweimao":
                            state["cwm_page"] += 1
                            state["cwm_last"] = r.get('is_last', False)
                        elif platform == "tomato":
                            state["tm_page"] += 1
                            state["tm_last"] = r.get('is_last', False)
                        state["raw_pool"].extend(books)
                    
                    # 拉取后重新计算评分，如果还是没结果且没到限制，继续循环拉取
                    _, _, current_avg = MultiSearchEngine.sift_by_average(state["raw_pool"], keyword, weights_map)
                    if not state["raw_pool"] and not all_exhausted:
                        continue
            
            # 筛选高质量结果并交叉排序
            # 注意：即使 current_avg < avg_threshold，只要池子里有东西，我们也进行一次筛选
            # 这样可以保证即使没有完美匹配，也能展示当前最接近的结果
            if state["raw_pool"]:
                good_batch, remains, _ = MultiSearchEngine.sift_by_average(state["raw_pool"], keyword, weights_map)
                if good_batch:
                    interleaved = MultiSearchEngine.interleave_results(good_batch, qd_prio, tm_prio, cwm_prio)
                    state["full_pool"].extend(interleaved)
                    state["raw_pool"] = remains
                else:
                    # 如果这一批次没有“高于平均分”的结果（理论上不可能，除非全0分）
                    # 则把 raw_pool 的内容强行按分数排序放入 full_pool
                    if all_exhausted or batch_count >= max_batches:
                        sorted_raw = sorted(state["raw_pool"], key=lambda x: x.get('final_score', 0), reverse=True)
                        state["full_pool"].extend(sorted_raw)
                        state["raw_pool"] = []
            elif all_exhausted:
                break

        # 如果是直接查看详情模式
        if direct_index is not None:
            if 1 <= direct_index <= len(state["full_pool"]):
                target = state["full_pool"][direct_index - 1]
                state["last_viewed"] = target # 记录最近查看
                details = await self.source_manager.get_source(target['origin']).get_book_details(target["url"])
                if details:
                    yield event.chain_result(await self._format_book_details(details))
                return
            else:
                yield event.plain_result(f"⚠️ 序号 {direct_index} 超出综合搜索结果范围（共 {len(state['full_pool'])} 条），将显示搜索列表。")

        # 计算当前页展示的结果范围
        start_idx = (req_page - 1) * self.page_size
        display_list = state["full_pool"][start_idx: start_idx + self.page_size]
        state["current_page"] = req_page

        # 无结果提示
        if not display_list:
            yield event.plain_result(f"抱歉，没有找到匹配“{keyword}”的高质量结果。")
            return

        # 1. 检查是否还能拉取更多数据（起点/刺猬猫/番茄未到最后一页）
        can_load_more = False
        if not state["qd_last"] or not state["cwm_last"] or not state["tm_last"]:
            can_load_more = True
        
        # 2. 计算当前总页数（已加载数据）
        current_total_pages = (len(state["full_pool"]) + self.page_size - 1) // self.page_size
        # 3. 判断是否有下一页（已加载够下一页 或 还能加载更多数据）
        has_next_page = False
        if req_page < current_total_pages:
            has_next_page = True
        elif can_load_more and (req_page + 1) * self.page_size > len(state["full_pool"]):
            has_next_page = True
        
        # 4. 构建消息（显示总页数）
        can_load_more = not state["qd_last"] or not state["cwm_last"] or not state["tm_last"]
        current_total_pages = (len(state["full_pool"]) + self.page_size - 1) // self.page_size
        
        if can_load_more:
            msg = f"以下是【{keyword}】的第 {req_page} 页综合搜索结果：\n"  # 有更多→只显示当前页
        else:
            msg = f"以下是【{keyword}】的第 {req_page}/{current_total_pages} 页综合搜索结果：\n"  # 无更多→显示总页数
        for i, b in enumerate(display_list):
            origin = b.get('origin')
            if origin == 'qidian':
                platform_tag = "[起点]"
            elif origin == 'ciweimao':
                platform_tag = "[刺猬猫]"
            elif origin == 'sfacg':
                platform_tag = "[菠萝包]"
            elif origin == 'tomato':
                platform_tag = "[番茄]"
            elif origin == 'faloo':
                platform_tag = "[飞卢]"
            else:
                platform_tag = "[未知]"
            msg += f"{start_idx + i + 1}. {b['name']}\n    {platform_tag} 作者：{b['author']}\n"
        
        # 5. 构建翻页提示
        page_tips = []
        page_tips.append(f"`/ss p`") if req_page > 1 else None
        page_tips.append(f"`/ss n`") if has_next_page else None
        
        logger.info(f"用户 {user_id} 搜索【{keyword}】第 {req_page} 页结果，当前池中共有 {len(state['full_pool'])} 条结果，可加载更多：{can_load_more}。")
        
        # 6. 补充操作提示
        msg += f"\n💡 `/ss <序号>` 查看详情\n"
        if page_tips:
            msg += f"💡 使用 {' | '.join(page_tips)} 翻页"
        else:
            if req_page == 1 and len(state["full_pool"]) <= self.page_size and not can_load_more:
                msg += "💡 当前已是全部结果，无更多内容"
            elif req_page > 1 and not has_next_page and not can_load_more:
                msg += "💡 当前已是最后一页，无更多内容"
        
        yield event.plain_result(msg)

    @filter.command("起点", alias={'qd'})
    async def qidian_handler(self, event: AstrMessageEvent):
        """起点中文网专属搜索"""
        async for res in self._common_handler(event, "qidian", "qd", "起点"):
            yield res

    @filter.command("刺猬猫", alias={'cwm'})
    async def ciweimao_handler(self, event: AstrMessageEvent):
        """刺猬猫专属搜索"""
        async for res in self._common_handler(event, "ciweimao", "cwm", "刺猬猫"):
            yield res

    @filter.command("番茄", alias={'fq'})
    async def tomato_handler(self, event: AstrMessageEvent):
        """番茄小说专属搜索"""
        if not self.config.get("tomato_api_base"):
            yield event.plain_result("❌ 未配置番茄 API 基础地址，请在配置中填写。")
            return
        async for res in self._common_handler(event, "tomato", "fq", "番茄"):
            yield res

    @filter.command("菠萝包", alias={'blb'})
    async def sfacg_handler(self, event: AstrMessageEvent):
        """菠萝包(SFACG)专属搜索"""
        async for res in self._common_handler(event, "sfacg", "blb", "菠萝包"):
            yield res

    @filter.command("飞卢", alias={'fl'})
    async def faloo_handler(self, event: AstrMessageEvent):
        """飞卢专属搜索"""
        async for res in self._common_handler(event, "faloo", "fl", "飞卢"):
            yield res

    @filter.command("七猫", alias={'qm', '纵横'})
    async def qimao_handler(self, event: AstrMessageEvent):
        """七猫专属搜索"""
        async for res in self._common_handler(event, "qimao", "qm", "七猫"):
            yield res

    @filter.command("三江", alias={'sj'})
    async def sanjiang_handler(self, event: AstrMessageEvent):
        """获取起点三江频道推荐书籍"""
        qidian = self.source_manager.get_source("qidian")
        books = await qidian.get_sanjiang_books()
        
        if not books:
            yield event.plain_result("❌ 暂时没有获取到三江推荐书籍，请稍后再试。")
            return
            
        # 记录到搜索状态，方便用户直接通过序号看详情
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)
        state.update({
            "keyword": "三江推荐",
            "source": "qidian",
            "full_pool": books, # 三江不需要翻页，直接放入全量池
            "results": books,
            "cached_pages": {1: books},
            "current_page": 1,
            "max_pages": 1,
            "last_viewed": None
        })
        
        msg = "📖 【起点·三江推荐】\n\n"
        for i, b in enumerate(books): # 一次性展示全部结果
            msg += f"{i+1}. {b['name']} | {b['author']}\n"
            msg += f"   分类：{b['cat']} | 状态：{b['state']} | {b['cnt']}\n"
            if b.get('rec'):
                msg += f"   评语：{b['rec']}\n"
            desc = b['desc'].replace('\r', '').replace('\n', '').strip()
            msg += f"   简介：{desc[:60]}...\n\n"
            
        msg += f"💡 共 {len(books)} 本。使用 `/qd <序号>` 查看详情。"
        
        yield event.plain_result(msg.strip())



    @filter.command("添加书架", alias={'加书架'})
    async def add_to_bookshelf(self, event: AstrMessageEvent):
        """添加书籍到书架"""
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)
        parts = event.message_str.strip().split()
        
        target_book = None
        
        # 如果提供了序号
        if len(parts) >= 2 and parts[1].isdigit():
            idx = int(parts[1]) - 1
            if 0 <= idx < len(state.get("full_pool", [])):
                target_book = state["full_pool"][idx]
            elif 0 <= idx < len(state.get("results", [])):
                target_book = state["results"][idx]
        # 如果没有参数，使用最近查看的书籍
        elif len(parts) == 1:
            target_book = state.get("last_viewed")
            
        if not target_book:
            yield event.plain_result("❌ 请提供有效的书籍序号，或先查看一本书的详情。")
            return
            
        success = self.bookshelf_manager.add_book(user_id, target_book)
        if success:
            yield event.plain_result(f"✅ 已将《{target_book['name']}》加入书架。")
        else:
            yield event.plain_result(f"🤔 《{target_book['name']}》已经在你的书架里了。")

    @filter.command("移除书架", alias={'删书'})
    async def remove_from_bookshelf(self, event: AstrMessageEvent):
        """从书架移除书籍"""
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)
        parts = event.message_str.strip().split()
        
        # 如果没有参数，尝试移除最近查看的书籍
        if len(parts) == 1:
            last_viewed = state.get("last_viewed")
            if last_viewed:
                success = self.bookshelf_manager.remove_book_by_info(user_id, last_viewed['bid'], last_viewed['origin'])
                if success:
                    yield event.plain_result(f"✅ 已将《{last_viewed['name']}》从书架移除。")
                    return
            yield event.plain_result("❌ 请提供书架中的书籍序号，或先通过书架查看一本书。")
            return

        # 如果提供了序号
        if parts[1].isdigit():
            idx = int(parts[1])
            removed = self.bookshelf_manager.remove_book(user_id, idx)
            if removed:
                yield event.plain_result(f"✅ 已将《{removed['name']}》从书架移除。")
            else:
                yield event.plain_result(f"❌ 书架中不存在序号为 {idx} 的书籍。")

    @filter.command("查看书架", alias={'书架','我的书架'})
    async def view_bookshelf(self, event: AstrMessageEvent):
        """查看个人书架"""
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)
        parts = event.message_str.strip().split()
        
        books = self.bookshelf_manager.get_bookshelf(user_id)
        if not books:
            yield event.plain_result("📂 你的书架空空如也，快去搜书添加吧！")
            return

        page_size = 20
        total_pages = (len(books) + page_size - 1) // page_size
        
        # 处理序号查看详情
        if len(parts) >= 2 and parts[1].isdigit():
            idx = int(parts[1])
            target = self.bookshelf_manager.get_book_by_index(user_id, idx)
            if target:
                state["last_viewed"] = target
                details = await self.source_manager.get_source(target['origin']).get_book_details(target["url"])
                if details:
                    yield event.chain_result(await self._format_book_details(details))
                return
            else:
                yield event.plain_result(f"❌ 书架中没有序号为 {idx} 的书籍。")
                return

        # 处理翻页
        req_page = state.get("bookshelf_page", 1)
        if len(parts) >= 2:
            action = parts[1]
            if action in ("下一页", "下页", "n", "next"):
                if req_page >= total_pages:
                    yield event.plain_result("🤔 已经到最后一页了。")
                    return
                req_page += 1
            elif action in ("上一页", "上页", "p", "prev"):
                if req_page <= 1:
                    yield event.plain_result("🤔 已经是第一页了。")
                    return
                req_page -= 1
        
        state["bookshelf_page"] = req_page
        
        start_idx = (req_page - 1) * page_size
        display_list = books[start_idx : start_idx + page_size]
        
        msg = f"📚 我的书架 (共 {len(books)} 本)\n\n"
        for i, b in enumerate(display_list):
            origin = b.get('origin')
            if origin == 'qidian':
                platform_tag = "[起点]"
            elif origin == 'ciweimao':
                platform_tag = "[刺猬猫]"
            elif origin == 'sfacg':
                platform_tag = "[菠萝包]"
            elif origin == 'tomato':
                platform_tag = "[番茄]"
            elif origin == 'faloo':
                platform_tag = "[飞卢]"
            elif origin == 'qimao':
                platform_tag = "[七猫]"
            else:
                platform_tag = "[未知]"
            msg += f"{start_idx + i + 1}. {b['name']}\n    {platform_tag} 作者：{b['author']}\n"
        
        msg += f"\n💡 `/书架 <序号>` 查看详情\n"
        
        # 动态构建翻页提示
        page_tips = []
        if req_page > 1:
            page_tips.append("p")
        if req_page < total_pages:
            page_tips.append("n")
            
        if page_tips:
            msg += f"💡 `/书架 {'/'.join(page_tips)}` 翻页\n"
            
        msg += f"💡 `/删书 <序号>` 删除书籍"
        
        yield event.plain_result(msg.strip())

    async def _get_page_data(self, state, source_name, keyword, target_page):
        """获取指定页码数据（优先读取缓存）
        
        Args:
            state: 用户搜索状态
            source_name: 数据源名称（qidian/ciweimao/tomato）
            keyword: 搜索关键词
            target_page: 目标页码
        
        Returns:
            list: 该页码的书籍列表
        """
        # 缓存命中：直接返回
        if target_page in state["cached_pages"]:
            return state["cached_pages"][target_page]
        
        # 缓存未命中：拉取数据并缓存
        source = self.source_manager.get_source(source_name)
        res = await source.search_book(keyword, page=target_page, return_metadata=True)
        page_data = res.get("books", [])
        state["cached_pages"][target_page] = page_data
        return page_data

    async def _common_handler(self, event: AstrMessageEvent, source_name: str, cmd_alias: str, platform_name: str):
        """单平台搜索通用处理逻辑
        
        Args:
            event: 消息事件对象
            source_name: 数据源名称（qidian/ciweimao/tomato）
            cmd_alias: 指令别名（qd/cwm/fq）
            platform_name: 平台显示名称（起点/刺猬猫/番茄）
        
        Yields:
            搜索结果/提示信息
        """
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result(f"请输入书名。用法: /{cmd_alias} <书名>\n💡 翻页: /{cmd_alias} n\n💡 详情: /{cmd_alias} <序号>")
            return
        
        # 解析用户ID和操作指令
        user_id = event.get_sender_id()
        action = parts[1]
        state = self._get_user_search_state(user_id)

        # 1. 序号查询：查看当前搜索结果池的书籍详情 (e.g. /qd 1)
        if action.isdigit() and len(parts) == 2:
            seq = int(action)
            if seq < 1:
                yield event.plain_result(f"🤔 序号 {seq} 无效。")
                return
            
            # 计算目标页码和页内索引
            target_page = (seq - 1) // self.page_size + 1
            page_inner_idx = (seq - 1) % self.page_size
            
            # 校验搜索状态
            if not state["keyword"] or state["source"] != source_name:
                yield event.plain_result(f"❌ 请先使用 /{cmd_alias} 搜索一本书。")
                return
            if target_page > state["max_pages"]:
                yield event.plain_result(f"🤔 序号 {seq} 不在当前结果中。")
                return
            
            # 获取目标页数据
            if target_page in state["cached_pages"]:
                page_data = state["cached_pages"][target_page]
            else:
                page_data = await self._get_page_data(state, source_name, state["keyword"], target_page)
            
            if not page_data or page_inner_idx >= len(page_data):
                yield event.plain_result(f"🤔 序号 {seq} 不在当前结果中。")
                return
            
            # 查询并返回书籍详情
            target_book = page_data[page_inner_idx]
            state["last_viewed"] = target_book # 记录最近查看
            details = await self.source_manager.get_source(source_name).get_book_details(target_book["url"])
            if details:
                yield event.chain_result(await self._format_book_details(details))
            return

        # 2. 翻页操作 (e.g. /qd 下一页)
        if action in ("下一页", "n", "next", "上一页", "p", "prev") and len(parts) == 2:
            if not state["keyword"] or state["source"] != source_name:
                yield event.plain_result(f"❌ 请先使用 /{cmd_alias} 搜索一本书。")
                return
            
            next_p = state["current_page"] + (1 if action in ("下一页", "n", "next") else -1)
            if next_p < 1 or next_p > state["max_pages"]:
                yield event.plain_result("➡️ 已经没有更多了。")
                return
            
            # 获取翻页数据
            page_data = await self._get_page_data(state, source_name, state["keyword"], next_p)
            state["current_page"] = next_p
            state["results"] = page_data
            
            # 发送翻页结果
            yield event.plain_result(self._build_search_message(
                state["keyword"], next_p, state["max_pages"], 
                page_data, cmd_alias, self.page_size, source_name
            ))
            return

        # 3. 首次搜索逻辑 或 直接查看详情 (e.g. /qd 诡秘之主 或 /qd 诡秘之主 1)
        # 解析直接查看详情索引
        direct_index = None
        if len(parts) >= 3 and parts[-1].isdigit():
            try:
                direct_index = int(parts[-1])
                book_name = " ".join(parts[1:-1])
            except ValueError:
                book_name = " ".join(parts[1:])
        else:
            book_name = " ".join(parts[1:])

        try:
            # 拉取第一页数据
            source = self.source_manager.get_source(source_name)
            res = await source.search_book(book_name, page=1, return_metadata=True)
            
            # 无结果提示
            if not res or not res.get("books"):
                yield event.plain_result(f"在{platform_name}找不到“{book_name}”。")
                return
            
            # 处理起点返回的100条数据
            first_page_data = res.get("books", [])
            
            # 清空旧的缓存页面，防止跨搜索/跨平台数据污染
            state["cached_pages"].clear()
            
            if source_name == "qidian":
                # 起点一次性返回100条，全部存入single_pool
                state["single_pool"] = first_page_data
                # 计算总页数（10条/页）
                state["max_pages"] = (len(first_page_data) + self.page_size - 1) // self.page_size
                # 缓存所有分页数据
                for i in range(state["max_pages"]):
                    start = i * self.page_size
                    end = start + self.page_size
                    state["cached_pages"][i+1] = first_page_data[start:end]
            else:
                state["max_pages"] = res.get("max_pages", 1)
                state["cached_pages"][1] = first_page_data
            
            # 更新用户搜索状态
            state.update({
                "keyword": book_name, 
                "current_page": 1, 
                "source": source_name,
                "results": first_page_data[:self.page_size],  # 只取前10条展示
                "last_viewed": None
            })

            # 如果是直接查看详情模式
            if direct_index is not None:
                if 1 <= direct_index <= len(first_page_data):
                    target_book = first_page_data[direct_index - 1]
                    state["last_viewed"] = target_book
                    details = await self.source_manager.get_source(source_name).get_book_details(target_book["url"])
                    if details:
                        yield event.chain_result(await self._format_book_details(details))
                    return
                else:
                    yield event.plain_result(f"⚠️ 序号 {direct_index} 超出结果范围 (1-{len(first_page_data)})，将显示搜索列表。")
            
            # 发送第一页结果
            yield event.plain_result(self._build_search_message(
                book_name, 1, state["max_pages"], 
                first_page_data[:self.page_size], cmd_alias, self.page_size, source_name
            ))
        except Exception as e:
            logger.error(f"{platform_name} Search Error: {e}")
            yield event.plain_result("⚠️ 搜索失败。")

    def _build_search_message(self, keyword, current_page, max_pages, results, cmd_alias, page_size, source_name=None):
        """构建单平台搜索结果消息
        
        Args:
            keyword: 搜索关键词
            current_page: 当前页码
            max_pages: 总页数
            results: 当前页结果列表
            cmd_alias: 指令别名
            page_size: 每页条数
            source_name: 数据源名称
        
        Returns:
            str: 格式化后的搜索结果消息
        """
        # 计算起始序号
        start_num = (current_page - 1) * page_size + 1
        
        if source_name == 'faloo':
            msg = f"以下是【{keyword}】的第 {current_page} 页搜索结果：\n"
        else:
            msg = f"以下是【{keyword}】的第 {current_page}/{max_pages} 页搜索结果：\n"
        
        for i, b in enumerate(results):
            msg += f"{start_num + i}. {b['name']}\n    作者：{b['author']}\n"
        
        # 补充操作提示
        msg += f"\n💡 `/{cmd_alias} <序号>` 查看详情\n"
        flip_tips = []
        if current_page > 1:
            flip_tips.append(f"`/{cmd_alias} p`")
        if current_page < max_pages:
            flip_tips.append(f"`/{cmd_alias} n`")
        
        if flip_tips:
            msg += f"💡 使用 {' | '.join(flip_tips)} 翻页"
        
        return msg

    def _truncate_trial_content(self, content):
        """截断试读内容（超出长度限制添加省略号）
        
        Args:
            content: 原始试读内容
        
        Returns:
            str: 截断后的试读内容
        """
        if not content:
            return ""
        cleaned_content = self._clean_text(content)
        if len(cleaned_content) <= self.trial_content_limit:
            return cleaned_content
        # 截断并处理结尾标点，保证语义完整
        truncated = cleaned_content[:self.trial_content_limit].rstrip()
        if truncated[-1] in [',', '.', '!', '?', ';', ':', '，', '。', '！', '？', '；', '：']:
            truncated = truncated[:-1]
        return f"{truncated}……"

    async def _format_book_details(self, details):
        """格式化书籍详情消息（含封面、基础信息、试读内容）
        
        Args:
            details: 书籍详情字典
        
        Returns:
            list: 消息链（图片+文本）
        """
        chain = []
        # 处理封面图片（base64编码）
        if details.get("cover") and details["cover"] not in ["无", None]:
            cover_url = details["cover"]
            try:
                session = await self.get_session()
                # 针对番茄小说的 URL 使用 encoded=True，防止 aiohttp 对已签名的 URL 进行二次编码
                # 番茄封面通常包含签名信息，二次编码会导致 403；图片代理 URL 同样含编码后的签名参数
                is_tomato = "p3-novel.byteimg.com" in cover_url or "p6-novel.byteimg.com" in cover_url or "p9-novel.byteimg.com" in cover_url or "fqnovelpic.com" in cover_url or "/api/img/proxy" in cover_url
                
                request_url = URL(cover_url, encoded=True) if is_tomato else cover_url
                
                async with session.get(request_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        image_bytes = await resp.read()
                        if image_bytes:
                            # HEIC 封面（番茄等源）IM 无法显示，转 JPEG
                            if image_bytes[4:8] == b"ftyp":
                                image_bytes = self._convert_heic_to_jpeg(image_bytes)
                            if image_bytes:
                                base64_str = base64.b64encode(image_bytes).decode()
                                chain.append(Comp.Image(file=f"base64://{base64_str}"))
                            else:
                                logger.warning(f"封面转码失败: {cover_url}")
                        else:
                            logger.warning(f"封面图片数据为空: {cover_url}")
                    else:
                        logger.warning(f"封面下载失败，状态码: {resp.status}, URL: {cover_url}")
            except Exception as e:
                logger.error(f"封面下载异常: {type(e).__name__} - {e}, URL: {cover_url}")
        
        # 构建基础信息
        msg = f"---【{details['name']}】---\n✍️ 作者: {details['author']}\n"
        if details.get('category'):
            msg += f"🏷️ 类型: {details['category']}\n"
        
        # 状态信息（强制转为字符串避免类型错误）
        status_p = []
        if details.get('status'):
            status_p.append(str(details['status']))
        if details.get('word_count'):
            status_p.append(str(details['word_count']))
        if details.get('total_chapters'):
            status_p.append(f"共 {details['total_chapters']}章")
        if status_p:
            msg += "🚦 状态: " + " | ".join(status_p) + "\n"
        
        # 详细模式补充信息
        if self.display_mode == "detailed":
            # 标签
            if details.get('tags'):
                msg += f"🔖 标签: {' / '.join(details['tags'])}\n"
            
            # 评分
            r, u = str(details.get('rating')), str(details.get('rating_users'))
            if r not in ["None", "0", "0.0", "暂无"]:
                if u not in ["None", "0"]:
                    msg += f"⭐ 评分: {r} ({u}人评价)\n"
                else:
                    msg += f"⭐ 评分: {r}\n"
            
            # 排行
            if details.get('rank') and details['rank'] != "未上榜":
                msg += f"🏆 排行: 月票榜第 {details['rank']} 名\n"
            
            # 热度（收藏/推荐/点击/飞卢数据）
            heat = []
            if details.get('collection') and str(details.get('collection')) != "0":
                heat.append(f"收藏 {details['collection']}")
            if details.get('all_recommend') and str(details.get('all_recommend')) != "0":
                label = "在读" if "fanqienovel.com" in details.get('url', '') else "推荐"
                heat.append(f"{label} {details['all_recommend']}")
            if details.get('total_click') and str(details.get('total_click')) != "0":
                heat.append(f"点击 {details['total_click']}")
            
            # 飞卢专属热度数据
            if details.get('origin') == 'faloo':
                for key, icon in [('reward_coin', '💰'), ('reward_flower', '🌹'), 
                                ('reward_ticket', '🎟️'), ('reward_review', '✍️')]:
                    val = details.get(key)
                    # 过滤 0, 0票, 0点 等零值，保留 0.5万 等非零值
                    if val and not re.match(r'^0[^\d\.]*$', str(val)):
                        heat.append(f"{icon} {val}")

            heat_str = " | ".join(heat)
            if heat_str:
                msg += f"🔥 热度: {heat_str}\n"
        
        # 简介
        if details.get('intro'):
            msg += f"📝 简介:\n{self._clean_text(details['intro'])}\n"
        
        # 最近更新
        if self.display_mode == "detailed" and details.get('last_update'):
            upd = f"🔄 最近更新: {details['last_update']}"
            if details.get('last_chapter'):
                upd += f" -> {details['last_chapter']}"
            msg += upd + "\n"
        
        # 链接（仅起点需要替换移动端为PC端）
        book_url = details['url']
        if "qidian.com" in book_url:
            book_url = book_url.replace('m.qidian.com', 'www.qidian.com')
        msg += f"🔗 链接: {book_url}\n"
        
        # 试读内容
        if self.enable_trial and details.get('first_chapter_title'):
            trial_content = self._truncate_trial_content(details.get('first_chapter_content', ''))
            msg += f"\n📖 【试读】{details['first_chapter_title']}\n{trial_content}\n"
        
        chain.append(Comp.Plain(msg.strip()))
        return chain

    def _clean_text(self, text):
        """清理文本格式（移除HTML标签、替换特殊字符）
        
        Args:
            text: 原始文本
        
        Returns:
            str: 格式化后的文本
        """
        if not text:
            return ""
        # 移除HTML标签
        text = re.sub(r'</?p>|<br\s*/?>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        # 替换HTML特殊字符
        text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
        # 清理空行并格式化缩进
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return "　　" + "\n　　".join(lines)

    @staticmethod
    def _convert_heic_to_jpeg(image_bytes):
        """HEIC/HEIF 图片转 JPEG（IM 不支持 HEIC 显示）"""
        try:
            import io
            from PIL import Image
            import pillow_heif
            pillow_heif.register_heif_opener()
            img = Image.open(io.BytesIO(image_bytes))
            img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"HEIC 转 JPEG 失败: {type(e).__name__} - {e}")
            return None

    async def terminate(self):
        """插件卸载回调"""
        # 关闭持久化会话
        if self._session and not self._session.closed:
            await self._session.close()
        # 清理缓存，释放内存
        self.user_search_state.clear()
        logger.info("网文搜索助手插件卸载，缓存已清理")
