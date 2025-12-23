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
        """起点指令处理"""
        async for res in self._common_handler(event, "qidian", "qd", "起点"):
            yield res

    @filter.command("刺猬猫", alias={'cwm'})
    async def ciweimao_main_handler(self, event: AstrMessageEvent):
        """刺猬猫指令处理"""
        async for res in self._common_handler(event, "ciweimao", "cwm", "刺猬猫"):
            yield res

    async def _common_handler(self, event: AstrMessageEvent, source_name: str, cmd_alias: str, platform_name: str):
        """通用平台指令处理器"""
        command_parts = event.message_str.strip().split()
        if len(command_parts) < 2:
            yield event.plain_result(f"请输入书名。用法: /{cmd_alias} <书名>\n💡 翻页: /{cmd_alias} 下/上页\n💡 详情: /{cmd_alias} <序号>")
            return

        user_id = event.get_sender_id()
        action = command_parts[1]
        source = self.source_manager.get_source(source_name)

        # 1. 处理翻页
        if action in ["下一页", "上一页"]:
            state = self._get_user_search_state(user_id)
            if not state["keyword"]:
                yield event.plain_result("请先搜索一本书。")
                return

            if action == "下一页":
                if state["current_page"] >= state["max_pages"]:
                    yield event.plain_result("➡️ 没有更多了。")
                    return
                next_p = state["current_page"] + 1
            else:
                if state["current_page"] <= 1:
                    yield event.plain_result("⬅️ 已经是第一页。")
                    return
                next_p = state["current_page"] - 1

            try:
                res = await source.search_book(state["keyword"], page=next_p, return_metadata=True)
                state.update({"current_page": next_p, "results": res["books"]})
                yield event.plain_result(self._build_search_message(state["keyword"], next_p, state["max_pages"], res["books"], cmd_alias, platform_name))
            except Exception as e:
                logger.error(f"{platform_name} Page Error: {e}")
                yield event.plain_result("翻页失败。")
            return

        # 2. 处理详情查看
        elif action.isdigit():
            state = self._get_user_search_state(user_id)
            results = state.get("results", [])
            idx = int(action)
            # 假设每页 20 条记录进行偏移计算
            offset = (state["current_page"] - 1) * 20
            local_idx = idx - offset
            
            if not results or local_idx < 1 or local_idx > len(results):
                yield event.plain_result(f"🤔 序号 {idx} 不在当前页面结果中。")
                return
            
            selected = results[local_idx - 1]
            details = await source.get_book_details(selected["url"])
            if details:
                yield event.chain_result(await self._format_book_details(details))
            else:
                yield event.plain_result("无法获取该书详情。")
            return

        # 3. 处理新搜索
        book_name = " ".join(command_parts[1:])
        yield event.plain_result(f"🔍 正在{platform_name}搜索《{book_name}》...")
        try:
            res = await source.search_book(book_name, page=1, return_metadata=True)
            if not res or not res.get("books"):
                yield event.plain_result(f"在{platform_name}找不到《{book_name}》。")
                return
            
            max_pages = (res.get("total", 0) + 19) // 20
            if max_pages == 0 and res.get("books"): max_pages = 1
            
            self.user_search_state[user_id] = {
                "keyword": book_name, 
                "current_page": 1, 
                "max_pages": max_pages, 
                "results": res["books"]
            }
            yield event.plain_result(self._build_search_message(book_name, 1, max_pages, res["books"], cmd_alias, platform_name))
        except Exception as e:
            logger.error(f"{platform_name} Search Error: {e}")
            yield event.plain_result("搜索失败。")

    def _build_search_message(self, keyword, current_page, max_pages, results, cmd_alias="qd", platform_name="起点"):
        msg = f"以下是【{platform_name}】中“{keyword}”的第 {current_page}/{max_pages} 页结果:\n"
        start_num = (current_page - 1) * 20 + 1
        for i, b in enumerate(results):
            msg += f"{start_num + i}. {b['name']}\n    作者：{b['author']}\n"
        msg += f"\n💡 请使用 `/{cmd_alias} <序号>` 查看详情\n💡 使用 /{cmd_alias} 下一页 翻页"
        return msg

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

        intro = self._clean_text(details.get('intro', ''))
        if intro: msg += f"📝 简介:\n{intro}\n"
        
        if self.display_mode == "detailed" and details.get('last_update'):
            msg += f"🔄 最近更新: {details['last_update']}"
            if details.get('last_chapter'): msg += f" -> {details['last_chapter']}"
            msg += "\n"
        
        # 链接转换逻辑
        final_url = details['url']
        if "qidian.com" in final_url:
            final_url = final_url.replace('m.qidian.com', 'www.qidian.com')
        msg += f"🔗 链接: {final_url}\n"

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