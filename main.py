import asyncio
import aiohttp
import base64
import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

from .sources import SourceManager
from .core.search_engine import MultiSearchEngine

@register(
    "astrbot_plugin_webnovel_info",
    "Foolllll",
    "网文搜索助手",
    "0.1.0",
    ""
)
class WebnovelInfoPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.source_manager = SourceManager()
        self.config = config or {}
        
        display_cfg = self.config.get("display_mode", "详细")
        self.display_mode = "concise" if display_cfg == "简洁" else "detailed"
        self.enable_trial = self.config.get("enable_trial", False)
        
        # 权重映射：1->1.5, 2->1.0, 0->禁用
        w_cfg = self.config.get("platform_weights", "1 2").split()
        self.weights = {
            "qidian": MultiSearchEngine.get_weight(w_cfg[0] if len(w_cfg) > 0 else "1"),
            "ciweimao": MultiSearchEngine.get_weight(w_cfg[1] if len(w_cfg) > 1 else "2")
        }
        
        self.user_search_state = {}

    def _get_user_search_state(self, user_id: str):
        if user_id not in self.user_search_state:
            self.user_search_state[user_id] = {
                "keyword": "", "current_page": 1, "max_pages": 1, 
                "results": [], "full_pool": [], "source": ""
            }
        return self.user_search_state[user_id]

    @filter.command("搜书")
    async def multi_search_handler(self, event: AstrMessageEvent):
        """综合搜书：动态双页采样与跨平台竞标排序"""
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法: /搜书 <书名> [页码]")
            return

        user_id = event.get_sender_id()
        keyword = parts[1]
        req_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        state = self._get_user_search_state(user_id)
        page_size = 10

        # 判断是否需要重新触发采样（新搜或池子耗尽）
        need_sample = (state["keyword"] != keyword) or (len(state["full_pool"]) < req_page * page_size)

        if need_sample:
            yield event.plain_result(f"🔍 正在多平台综合搜索“{keyword}”...")
            
            # 双页采样：获取当前页和下一页数据以供缓冲
            tasks = []
            for p in [req_page, req_page + 1]:
                if self.weights["qidian"] > 0:
                    tasks.append(self.source_manager.get_source("qidian").search_book(keyword, page=p))
                if self.weights["ciweimao"] > 0:
                    tasks.append(self.source_manager.get_source("ciweimao").search_book(keyword, page=p))
            
            raw_res = await asyncio.gather(*tasks)
            new_batch = []
            for r in raw_res:
                books = r.get('books', []) if isinstance(r, dict) else r
                new_batch.extend(books)

            # 调用引擎进行评分、过滤和排序
            processed = MultiSearchEngine.rank_results(new_batch, keyword, self.weights["qidian"], self.weights["ciweimao"])
            
            if state["keyword"] != keyword:
                state["full_pool"] = processed
            else:
                # 翻页时合并并去重
                existing_urls = {b['url'] for b in state["full_pool"]}
                for b in processed:
                    if b['url'] not in existing_urls: state["full_pool"].append(b)
                state["full_pool"] = sorted(state["full_pool"], key=lambda x: x['final_score'], reverse=True)
            
            state["keyword"] = keyword
            state["source"] = "multi"

        start_idx = (req_page - 1) * page_size
        display_list = state["full_pool"][start_idx : start_idx + page_size]
        state["current_page"] = req_page

        if not display_list:
            yield event.plain_result(f"抱歉，没有找到更多关于“{keyword}”的结果。")
            return

        msg = f"以下是【{keyword}】的第 {req_page} 页综合搜索结果:\n"
        for i, b in enumerate(display_list):
            tag = "[起点]" if b['origin'] == 'qidian' else "[刺猬猫]"
            msg += f"{start_idx + i + 1}. {tag} {b['name']}\n    作者：{b['author']}\n"
        msg += f"\n💡 使用 /qd <序号> 或 /cwm <序号> 看详情\n💡 使用 /搜书 {keyword} {req_page + 1} 翻页"
        yield event.plain_result(msg)

    @filter.command("起点", alias={'qd'})
    async def qidian_handler(self, event: AstrMessageEvent):
        async for res in self._common_handler(event, "qidian", "qd", "起点"): yield res

    @filter.command("刺猬猫", alias={'cwm'})
    async def ciweimao_handler(self, event: AstrMessageEvent):
        async for res in self._common_handler(event, "ciweimao", "cwm", "刺猬猫"): yield res

    async def _common_handler(self, event: AstrMessageEvent, source_name: str, cmd_alias: str, platform_name: str):
        """通用指令处理器：支持详情路由"""
        command_parts = event.message_str.strip().split()
        if len(command_parts) < 2:
            yield event.plain_result(f"请输入书名。用法: /{cmd_alias} <书名>\n💡 翻页: /{cmd_alias} 下一页\n💡 详情: /{cmd_alias} <序号>")
            return

        user_id = event.get_sender_id()
        action = command_parts[1]
        state = self._get_user_search_state(user_id)
        page_size = 10 

        # 详情路由逻辑
        if action.isdigit():
            idx = int(action) - 1
            # 1. 优先路由：如果当前处于综合搜索状态
            if state.get("source") == "multi" and 0 <= idx < len(state["full_pool"]):
                target = state["full_pool"][idx]
                source = self.source_manager.get_source(target['origin'])
                details = await source.get_book_details(target["url"])
                if details: yield event.chain_result(await self._format_book_details(details))
                return
            
            # 2. 次选路由：单平台搜索状态
            offset = (state["current_page"] - 1) * page_size
            local_idx = idx if source_name == "qidian" else idx - offset
            if state.get("results") and 0 <= local_idx < len(state["results"]):
                source = self.source_manager.get_source(source_name)
                details = await source.get_book_details(state["results"][local_idx]["url"])
                if details: yield event.chain_result(await self._format_book_details(details))
                return
            
            yield event.plain_result(f"🤔 序号 {action} 不在显示范围内。")
            return

        # 独立平台搜索或翻页
        source = self.source_manager.get_source(source_name)
        if action in ["下一页", "上一页"]:
            if not state["keyword"] or state["source"] != source_name:
                yield event.plain_result(f"❌ 请先搜索一本书。")
                return
            next_p = state["current_page"] + (1 if action == "下一页" else -1)
            if next_p < 1 or next_p > state["max_pages"]:
                yield event.plain_result("➡️ 已经没有更多了。")
                return
            if source_name == "qidian":
                state["current_page"] = next_p
                yield event.plain_result(self._build_search_message(state["keyword"], next_p, state["max_pages"], state["results"], cmd_alias, page_size))
            else:
                res = await source.search_book(state["keyword"], page=next_p, return_metadata=True)
                state.update({"current_page": next_p, "results": res["books"]})
                yield event.plain_result(self._build_search_message(state["keyword"], next_p, state["max_pages"], res["books"], cmd_alias, page_size))
        else:
            book_name = " ".join(command_parts[1:])
            yield event.plain_result(f"🔍 正在{platform_name}搜索“{book_name}”...") 
            try:
                res = await source.search_book(book_name, page=1, return_metadata=True)
                if not res or not res.get("books"):
                    yield event.plain_result(f"在{platform_name}找不到“{book_name}”。")
                    return
                total = res.get("total", len(res["books"]))
                max_pages = (total + (page_size - 1)) // page_size
                if source_name == "qidian" and max_pages > 10: max_pages = 10
                self.user_search_state[user_id] = {
                    "keyword": book_name, "current_page": 1, "max_pages": max_pages, 
                    "results": res["books"], "source": source_name
                }
                display_results = res["books"][:page_size] if source_name == "qidian" else res["books"]
                yield event.plain_result(self._build_search_message(book_name, 1, max_pages, display_results, cmd_alias, page_size))
            except Exception as e:
                logger.error(f"{platform_name} Search Error: {e}")
                yield event.plain_result("⚠️ 搜索失败。")

    def _build_search_message(self, keyword, current_page, max_pages, results, cmd_alias, page_size):
        msg = f"以下是【{keyword}】的第 {current_page}/{max_pages} 页搜索结果:\n"
        start_num = (current_page - 1) * page_size + 1
        display_list = results[(current_page-1)*page_size : current_page*page_size] if len(results) > page_size else results
        for i, b in enumerate(display_list):
            msg += f"{start_num + i}. {b['name']}\n    作者：{b['author']}\n"
        msg += f"\n💡 使用 `/{cmd_alias} <序号>` 看详情\n💡 使用 /{cmd_alias} 下一页 翻页"
        return msg

    async def _format_book_details(self, details):
        chain = []
        if details.get("cover") and details["cover"] not in ["无", None]:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(details["cover"], timeout=10) as resp:
                        image_bytes = await resp.read()
                chain.append(Comp.Image(file=f"base64://{base64.b64encode(image_bytes).decode()}"))
            except: pass

        msg = f"---【{details['name']}】---\n✍️ 作者: {details['author']}\n"
        if details.get('category'): msg += f"🏷️ 类型: {details['category']}\n"
        status_p = [details[k] for k in ['status', 'word_count'] if details.get(k)]
        if details.get('total_chapters'): status_p.append(f"共 {details['total_chapters']}章")
        if status_p: msg += "🚦 状态: " + " | ".join(status_p) + "\n"

        if self.display_mode == "detailed":
            if details.get('tags'): msg += f"🔖 标签: {' / '.join(details['tags'])}\n"
            rating, r_users = str(details.get('rating')), str(details.get('rating_users'))
            if rating not in ["None", "0", "0.0", "暂无"] and r_users not in ["None", "0"]:
                msg += f"⭐ 评分: {rating} ({r_users}人评价)\n"
            if details.get('rank') and details.get('rank') != "未上榜": 
                msg += f"🏆 排行: 月票榜第 {details['rank']} 名\n"
            heat_parts = []
            if details.get('collection') and str(details.get('collection')) != "0": 
                heat_parts.append(f"收藏 {details['collection']}")
            if details.get('all_recommend') and str(details.get('all_recommend')) != "0": 
                heat_parts.append(f"推荐 {details['all_recommend']}")
            if heat_parts: msg += f"🔥 热度: {' | '.join(heat_parts)}\n"

        # 简介：无论模式，完整显示
        intro = self._clean_text(details.get('intro', ''))
        if intro: msg += f"📝 简介:\n{intro}\n"
        
        # 详细模式下显示更新信息
        if self.display_mode == "detailed" and details.get('last_update'):
            upd_msg = f"🔄 最近更新: {details['last_update']}"
            if details.get('last_chapter'): upd_msg += f" -> {details['last_chapter']}"
            msg += upd_msg + "\n"
        
        msg += f"🔗 链接: {details['url'].replace('m.qidian.com', 'www.qidian.com')}\n"

        if self.enable_trial and details.get('first_chapter_title'):
            msg += f"\n📖 【试读】{details['first_chapter_title']}\n"
            msg += f"{self._clean_text(details.get('first_chapter_content', ''))}\n"

        chain.append(Comp.Plain(msg.strip()))
        return chain

    def _clean_text(self, text):
        if not text: return ""
        text = re.sub(r'</?p>|<br\s*/?>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return "　　" + "\n　　".join(lines)

    async def terminate(self):
        logger.info("网文查询插件卸载")