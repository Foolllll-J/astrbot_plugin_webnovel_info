import asyncio
import aiohttp
import base64
import re
from yarl import URL
from cachetools import TTLCache
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

from .sources import SourceManager
from .core.search_engine import MultiSearchEngine

@register("astrbot_plugin_webnovel_info", "Foolllll", "网文信息搜索助手", "0.1", "")
class WebnovelInfoPlugin(Star):
    """网文搜索插件核心类
    支持多平台书籍搜索、分页、详情查看、试读内容展示
    """
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.source_manager = SourceManager()  # 数据源管理器
        self.config = config or {}             # 插件配置（默认空字典）
        
        # 显示模式：简洁/详细（默认详细）
        self.display_mode = "concise" if self.config.get("display_mode", "详细") == "简洁" else "detailed"
        self.enable_trial = self.config.get("enable_trial", False)  # 是否启用试读功能
        self.priority_cfg = self.config.get("platform_weights", "1 2").split()  # 平台权重配置
        
        # 初始化番茄 API 配置
        if "tomato" in self.source_manager.sources:
            self.source_manager.get_source("tomato").api_base = self.config.get("tomato_api_base", "")

        self.user_search_state = TTLCache(maxsize=1000, ttl=3600)
        
        self.trial_content_limit = 3000  # 试读内容长度限制（字符数）
        self.page_size = 10  

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
                "qd_last": False,       # 起点是否最后一页
                "cwm_last": False,      # 刺猬猫是否最后一页
                "source": "",           # 当前搜索源（multi/qidian/ciweimao）
                "max_pages": 1,         # 总页数
                "results": [],          # 当前页结果
                "single_pool": [],      # 单平台结果池
                "cached_pages": {}      # 页码缓存（key:页码，value:该页数据）
            }
        return self.user_search_state[user_id]

    @filter.command("搜书", alias={'ss'})
    async def multi_search_handler(self, event: AstrMessageEvent):
        """多平台综合搜索处理函数
        支持指令：/ss <书名> | /ss <序号> | /ss 上一页/下一页
        
        Args:
            event: 消息事件对象
        
        Yields:
            搜索结果/提示信息
        """
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法: /ss <书名> 或 /ss <序号> 或 /ss 下一页")
            return

        # 解析用户ID和操作指令
        user_id, action = event.get_sender_id(), parts[1]
        state = self._get_user_search_state(user_id)
        avg_threshold = 60  # 结果筛选阈值

        # 序号查询：查看指定书籍详情
        if action.isdigit():
            idx = int(action) - 1
            if 0 <= idx < len(state["full_pool"]):
                target = state["full_pool"][idx]
                details = await self.source_manager.get_source(target['origin']).get_book_details(target["url"])
                if details:
                    yield event.chain_result(await self._format_book_details(details))
                return
            yield event.plain_result(f"🤔 序号 {action} 不在当前结果中。")
            return

        # 翻页操作
        if action in ["下一页", "下页", "上一页", "上页"]:
            if not state["keyword"]:
                yield event.plain_result("❌ 请先搜索。")
                return
            req_page = state["current_page"] + (1 if action in ["下一页", "下页"] else -1)
            if req_page < 1:
                yield event.plain_result("⬅️ 已经是第一页。")
                return
            keyword = state["keyword"]
        # 新关键词搜索
        else:
            keyword = " ".join(parts[1:])
            req_page = 1
            if state["keyword"] != keyword:
                yield event.plain_result(f"🔍 正在多平台搜索“{keyword}”...")
                # 重置搜索状态
                state.update({
                    "keyword": keyword, "full_pool": [], "raw_pool": [], 
                    "qd_page": 1, "cwm_page": 1, "qd_last": False, 
                    "cwm_last": False, "source": "multi",
                    "cached_pages": {}
                })

        # 计算目标页数需要的结果总数
        target_count = req_page * self.page_size
        qd_prio, cwm_prio = self.priority_cfg[0], self.priority_cfg[1]
        weights_map = {
            "qidian": MultiSearchEngine.get_weight(qd_prio), 
            "ciweimao": MultiSearchEngine.get_weight(cwm_prio)
        }

        # 补充结果池直到满足目标页数需求
        while len(state["full_pool"]) < target_count:
            _, _, current_avg = MultiSearchEngine.sift_by_average(state["raw_pool"], keyword, weights_map)
            # 结果不足或质量不达标时，拉取更多数据
            if not state["raw_pool"] or (current_avg < avg_threshold and not (state["qd_last"] and state["cwm_last"])):
                tasks, p_map = [], []
                # 起点搜索任务
                if qd_prio != "0" and not state["qd_last"]:
                    tasks.append(self.source_manager.get_source("qidian").search_book(keyword, page=state["qd_page"], return_metadata=True))
                    p_map.append("qidian")
                # 刺猬猫搜索任务
                if cwm_prio != "0" and not state["cwm_last"]:
                    tasks.append(self.source_manager.get_source("ciweimao").search_book(keyword, page=state["cwm_page"], return_metadata=True))
                    p_map.append("ciweimao")
                if not tasks:
                    break
                # 并发执行搜索任务
                results = await asyncio.gather(*tasks)
                for i, r in enumerate(results):
                    if not r:
                        continue
                    books = r.get('books', [])
                    # 更新平台页码和是否最后一页状态
                    if p_map[i] == "qidian":
                        state["qd_page"] += 1
                        state["qd_last"] = r.get('is_last', False)
                    else:
                        state["cwm_page"] += 1
                        state["cwm_last"] = r.get('is_last', False)
                    state["raw_pool"].extend(books)
                continue

            # 筛选高质量结果并交叉排序
            good_batch, remains, _ = MultiSearchEngine.sift_by_average(state["raw_pool"], keyword, weights_map)
            if good_batch:
                interleaved = MultiSearchEngine.interleave_results(good_batch, qd_prio, cwm_prio)
                state["full_pool"].extend(interleaved)
                state["raw_pool"] = remains
            else:
                # 无高质量结果时，补充剩余原始结果
                if state["qd_last"] and state["cwm_last"]:
                    state["full_pool"].extend(state["raw_pool"])
                    state["raw_pool"] = []
                    break
                state["raw_pool"] = []

        # 计算当前页展示的结果范围
        start_idx = (req_page - 1) * self.page_size
        display_list = state["full_pool"][start_idx: start_idx + self.page_size]
        state["current_page"] = req_page

        # 无结果提示
        if not display_list:
            yield event.plain_result(f"抱歉，没有找到匹配“{keyword}”的高质量结果。")
            return

        # 1. 检查是否还能拉取更多数据（起点/刺猬猫未到最后一页）
        can_load_more = False
        if not state["qd_last"] or not state["cwm_last"]:
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
        can_load_more = not state["qd_last"] or not state["cwm_last"]
        current_total_pages = (len(state["full_pool"]) + self.page_size - 1) // self.page_size
        
        if can_load_more:
            msg = f"以下是【{keyword}】的第 {req_page} 页综合搜索结果：\n"  # 有更多→只显示当前页
        else:
            msg = f"以下是【{keyword}】的第 {req_page}/{current_total_pages} 页综合搜索结果：\n"  # 无更多→显示总页数
        for i, b in enumerate(display_list):
            platform_tag = "[起点]" if b.get('origin') == 'qidian' else ("[刺猬猫]" if b.get('origin') == 'ciweimao' else "[番茄]")
            msg += f"{start_idx + i + 1}. {b['name']}\n    {platform_tag} 作者：{b['author']}\n"
        
        # 5. 构建翻页提示
        page_tips = []
        page_tips.append(f"/ss 上一页") if req_page > 1 else None
        page_tips.append(f"/ss 下一页") if has_next_page else None
        
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
            yield event.plain_result(f"请输入书名。用法: /{cmd_alias} <书名>\n💡 翻页: /{cmd_alias} 下一页\n💡 详情: /{cmd_alias} <序号>")
            return
        
        # 解析用户ID和操作指令
        user_id = event.get_sender_id()
        action = parts[1]
        state = self._get_user_search_state(user_id)

        # 序号查询：查看书籍详情
        if action.isdigit():
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
            details = await self.source_manager.get_source(source_name).get_book_details(target_book["url"])
            if details:
                yield event.chain_result(await self._format_book_details(details))
            return

        # 翻页操作
        if action in ["下一页", "上一页"]:
            if not state["keyword"] or state["source"] != source_name:
                yield event.plain_result(f"❌ 请先使用 /{cmd_alias} 搜索一本书。")
                return
            
            next_p = state["current_page"] + (1 if action == "下一页" else -1)
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

        # 首次搜索逻辑
        book_name = " ".join(parts[1:])
        yield event.plain_result(f"🔍 正在{platform_name}搜索“{book_name}”...") 
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
                "results": first_page_data[:self.page_size]  # 只取前10条展示
            })
            
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
        
        msg = f"以下是【{keyword}】的第 {current_page}/{max_pages} 页搜索结果：\n"
        for i, b in enumerate(results):
            msg += f"{start_num + i}. {b['name']}\n    作者：{b['author']}\n"
        
        # 补充操作提示
        msg += f"\n💡 `/{cmd_alias} <序号>` 查看详情\n"
        flip_tips = []
        if current_page > 1:
            flip_tips.append(f"/{cmd_alias} 上一页")
        if current_page < max_pages:
            flip_tips.append(f"/{cmd_alias} 下一页")
        
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
                # 关键：使用 yarl.URL(encoded=True) 防止 aiohttp 自动对已签名的 URL 进行二次编码导致 403
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                }
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(URL(cover_url, encoded=True), timeout=10) as resp:
                        if resp.status == 200:
                            image_bytes = await resp.read()
                            chain.append(Comp.Image(file=f"base64://{base64.b64encode(image_bytes).decode()}"))
                        else:
                            logger.warning(f"封面下载失败，状态码: {resp.status}, URL: {cover_url}")
            except Exception as e:
                logger.error(f"封面下载异常: {e}")
                pass
        
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
            
            # 热度（收藏/推荐）
            heat = []
            if details.get('collection') and str(details.get('collection')) != "0":
                heat.append(f"收藏 {details['collection']}")
            if details.get('all_recommend') and str(details.get('all_recommend')) != "0":
                label = "在读" if "fanqienovel.com" in details.get('url', '') else "推荐"
                heat.append(f"{label} {details['all_recommend']}")
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

    async def terminate(self):
        """插件卸载回调"""
        # 清理缓存，释放内存
        self.user_search_state.clear()
        logger.info("网文信息搜索助手插件卸载，缓存已清理")