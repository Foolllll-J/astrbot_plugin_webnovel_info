import asyncio
import aiohttp
import base64
import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

from .sources import SourceManager

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
        
        self.user_search_state = {}

    def _get_user_search_state(self, user_id: str):
        if user_id not in self.user_search_state:
            self.user_search_state[user_id] = {"keyword": "", "current_page": 1, "max_pages": 1, "results": []}
        return self.user_search_state[user_id]

    @filter.command("起点", alias={'qd'})
    async def qidian_main_handler(self, event: AstrMessageEvent):
        command_parts = event.message_str.strip().split()
        if len(command_parts) < 2:
            yield event.plain_result("请输入书名。用法: /qd <书名>\n💡 翻页: /qd 下一页\n💡 详情: /qd <序号>")
            return

        user_id = event.get_sender_id()
        action = command_parts[1]
        source = self.source_manager.get_source("qidian")

        if action == "下一页":
            async for res in self.qidian_next_page_common(event): yield res
            return
        elif action == "上一页":
            async for res in self.qidian_prev_page_common(event): yield res
            return
        elif action.isdigit():
            state = self._get_user_search_state(user_id)
            results = state.get("results", [])
            idx = int(action)
            offset = (state["current_page"] - 1) * 20
            local_idx = idx - offset
            
            if not results or local_idx < 1 or local_idx > len(results):
                yield event.plain_result(f"🤔 序号 {idx} 不在当前页面。")
                return
            
            selected = results[local_idx - 1]
            details = await source.get_book_details(selected["url"])
            if details:
                yield event.chain_result(await self._format_book_details(details))
            return

        book_name = " ".join(command_parts[1:])
        yield event.plain_result(f"🔍 正在起点搜索《{book_name}》...")
        try:
            res = await source.search_book(book_name, page=1, return_metadata=True)
            if not res or not res.get("books"):
                yield event.plain_result(f"在起点找不到《{book_name}》。")
                return
            max_pages = (res["total"] + 19) // 20
            self.user_search_state[user_id] = {"keyword": book_name, "current_page": 1, "max_pages": max_pages, "results": res["books"]}
            yield event.plain_result(self._build_search_message(book_name, 1, max_pages, res["books"]))
        except Exception as e:
            logger.error(f"Search Error: {e}")
            yield event.plain_result("搜索失败。")

    def _build_search_message(self, keyword, current_page, max_pages, results):
        msg = f"以下是【{keyword}】的第 {current_page}/{max_pages} 页搜索结果:\n"
        start_num = (current_page - 1) * 20 + 1
        for i, b in enumerate(results):
            msg += f"{start_num + i}. {b['name']}\n    作者：{b['author']}\n"
        msg += f"\n💡 请使用 `/qd <序号>` 查看详情\n💡 使用 /qd 下一页 翻页"
        return msg

    async def qidian_next_page_common(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)
        if not state["keyword"] or state["current_page"] >= state["max_pages"]:
            yield event.plain_result("➡️ 没有更多了。")
            return
        next_p = state["current_page"] + 1
        source = self.source_manager.get_source("qidian")
        res = await source.search_book(state["keyword"], page=next_p, return_metadata=True)
        state.update({"current_page": next_p, "results": res["books"]})
        yield event.plain_result(self._build_search_message(state["keyword"], next_p, state["max_pages"], res["books"]))

    async def qidian_prev_page_common(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)
        if state["current_page"] <= 1:
            yield event.plain_result("⬅️ 已经是第一页。")
            return
        prev_p = state["current_page"] - 1
        source = self.source_manager.get_source("qidian")
        res = await source.search_book(state["keyword"], page=prev_p, return_metadata=True)
        state.update({"current_page": prev_p, "results": res["books"]})
        yield event.plain_result(self._build_search_message(state["keyword"], prev_p, state["max_pages"], res["books"]))

    async def _format_book_details(self, details):
        chain = []
        if details.get("cover"):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(details["cover"], timeout=10) as resp:
                        image_bytes = await resp.read()
                chain.append(Comp.Image(file=f"base64://{base64.b64encode(image_bytes).decode()}"))
            except: pass

        msg = f"---【{details['name']}】---\n"
        msg += f"✍️ 作者: {details['author']}\n"
        if details.get('category'): msg += f"🏷️ 类型: {details['category']}\n"
        
        status_parts = []
        if details.get('status'): status_parts.append(details['status'])
        if details.get('word_count'): status_parts.append(details['word_count'])
        if details.get('total_chapters'): status_parts.append(f"共 {details['total_chapters']}章")
        if status_parts: msg += "🚦 状态: " + " | ".join(status_parts) + "\n"

        if self.display_mode == "detailed":
            if details.get('tags'): msg += f"🔖 标签: {' / '.join(details['tags'])}\n"
            
            rating, r_users = str(details.get('rating')), str(details.get('rating_users'))
            if rating not in ["0", "0.0", "暂无", "None"] and r_users not in ["0", "None"]:
                msg += f"⭐ 评分: {rating} ({r_users}人评价)\n"
            
            # 排行
            if details.get('rank') and details.get('rank') != "未上榜":
                msg += f"🏆 排行: 月票榜第 {details['rank']} 名\n"
            
            heat_parts = []
            if details.get('collection') and str(details.get('collection')) != "0": 
                heat_parts.append(f"收藏 {details['collection']}")
            if details.get('all_recommend') and str(details.get('all_recommend')) != "0": 
                heat_parts.append(f"推荐 {details['all_recommend']}")
            if heat_parts: msg += f"🔥 热度: {' | '.join(heat_parts)}\n"

        intro = self._clean_text(details.get('intro', ''))
        if intro: msg += f"📝 简介:\n{intro}\n"
        
        if self.display_mode == "detailed" and details.get('last_update'):
            msg += f"🔄 最近更新: {details['last_update']}"
            if details.get('last_chapter'): msg += f" -> {details['last_chapter']}"
            msg += "\n"
        
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
        lines = text.split('\n')
        cleaned_lines = [line.rstrip() for line in lines if line.strip() or line.startswith('　　')]
        return '\n'.join(cleaned_lines)

    async def terminate(self):
        logger.info("网文查询插件卸载")