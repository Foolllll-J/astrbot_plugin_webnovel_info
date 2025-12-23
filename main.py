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
    "网文信息查询",
    "0.1.0",
    ""
)
class WebnovelInfoPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.source_manager = SourceManager()
        self.config = config or {}
        self.user_search_state = {}

    def _get_user_search_state(self, user_id: str):
        if user_id not in self.user_search_state:
            self.user_search_state[user_id] = {"keyword": "", "current_page": 1, "max_pages": 1, "results": []}
        return self.user_search_state[user_id]

    @filter.command("起点", alias={'qd'})
    async def qidian_main_handler(self, event: AstrMessageEvent):
        """起点搜索、翻页、详情选择统一入口"""
        command_text = event.message_str.strip()
        command_parts = command_text.split()

        if len(command_parts) < 2:
            yield event.plain_result("请输入书名。用法: /qd <书名>\n💡 翻页: /qd 下一页\n💡 详情: /qd <序号>")
            return

        user_id = event.get_sender_id()
        action = command_parts[1]
        source = self.source_manager.get_source("qidian")

        # 1. 优先处理控制指令：下一页/上一页
        if action == "下一页":
            async for res in self.qidian_next_page_common(event): yield res
            return
        elif action == "上一页":
            async for res in self.qidian_prev_page_common(event): yield res
            return

        # 2. 处理序号选择 (例如 /qd 1)
        if action.isdigit():
            state = self._get_user_search_state(user_id)
            results = state.get("results", [])
            idx = int(action)
            if not results:
                yield event.plain_result("🤔 请先搜索书籍后再选择序号。")
                return
            
            if idx < 1 or idx > len(results):
                yield event.plain_result(f"❌ 序号超出范围。当前页面可用序号: 1-{len(results)}")
                return
            
            selected = results[idx - 1]
            yield event.plain_result(f"📚 正在调取《{selected['name']}》的档案...")
            details = await source.get_book_details(selected["url"])
            if details:
                yield event.chain_result(await self._format_book_details(details))
            else:
                yield event.plain_result("😢 档案读取失败。")
            return

        # 3. 默认逻辑：执行新搜索
        book_name = " ".join(command_parts[1:])
        yield event.plain_result(f"🔍 正在搜索起点《{book_name}》...")

        try:
            # 执行搜索，获取第一页
            res = await source.search_book(book_name, page=1, return_metadata=True)
            if not res or not res.get("books"):
                yield event.plain_result(f"在起点找不到《{book_name}》。")
                return

            # 根据 source 返回的 total 动态计算页数 (Source内部已封顶100，此处计算结果max_pages最大为5)
            max_pages = (res["total"] + 19) // 20
            if res["is_last"]: max_pages = 1

            # 存储状态
            self.user_search_state[user_id] = {
                "keyword": book_name,
                "current_page": 1,
                "max_pages": max_pages,
                "results": res["books"]
            }
            
            # 构造列表消息
            msg = f"【{book_name}】搜索结果 ({1}/{max_pages}):\n"
            for i, b in enumerate(res["books"]):
                msg += f"{i+1}. {b['name']} | {b['author']}\n"
            msg += f"\n💡 输入 /qd <序号> 查看详情"
            if max_pages > 1:
                msg += f"\n💡 输入 /qd 下一页 翻页"
            
            yield event.plain_result(msg)

        except Exception as e:
            logger.error(f"Search Process Error: {e}", exc_info=True)
            yield event.plain_result("搜索遇到了一些故障。")

    async def qidian_next_page_common(self, event: AstrMessageEvent):
        """通用下一页处理逻辑"""
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)
        if not state["keyword"] or state["current_page"] >= state["max_pages"]:
            yield event.plain_result("➡️ 后面没有了。")
            return

        next_p = state["current_page"] + 1
        source = self.source_manager.get_source("qidian")
        res = await source.search_book(state["keyword"], page=next_p, return_metadata=True)
        
        state.update({"current_page": next_p, "results": res["books"]})
        msg = f"【{state['keyword']}】搜索结果 ({next_p}/{state['max_pages']}):\n"
        for i, b in enumerate(res["books"]):
            msg += f"{i+1}. {b['name']} | {b['author']}\n"
        yield event.plain_result(msg)

    async def qidian_prev_page_common(self, event: AstrMessageEvent):
        """通用上一页处理逻辑"""
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)
        if state["current_page"] <= 1:
            yield event.plain_result("⬅️ 已经是第一页了。")
            return

        prev_p = state["current_page"] - 1
        source = self.source_manager.get_source("qidian")
        res = await source.search_book(state["keyword"], page=prev_p, return_metadata=True)
        
        state.update({"current_page": prev_p, "results": res["books"]})
        msg = f"【{state['keyword']}】搜索结果 ({prev_p}/{state['max_pages']}):\n"
        for i, b in enumerate(res["books"]):
            msg += f"{i+1}. {b['name']} | {b['author']}\n"
        yield event.plain_result(msg)

    async def _format_book_details(self, details):
        """将获取到的深度属性格式化为图文消息链"""
        chain = []
        # 封面图
        if details.get("cover"):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(details["cover"], timeout=10) as resp:
                        image_bytes = await resp.read()
                chain.append(Comp.Image(file=f"base64://{base64.b64encode(image_bytes).decode()}"))
            except: pass

        # 详细文本构建
        msg = f"---【{details['name']}】---\n"
        msg += f"作者: {details['author']} | 类型: {details['category']}\n"
        msg += f"状态: {details['status']} | {details['word_count']} | 共 {details['total_chapters']}章\n"
        msg += f"评分: {details['rating']} ({details['rating_users']}人) | 榜单: {details['rank']}\n"
        
        if details.get('tags'):
            msg += f"标签: {' / '.join(details['tags'])}\n"
        
        msg += f"热度: 收藏 {details['collection']} | 推荐 {details['all_recommend']}\n"
        
        # 简介
        intro = self._clean_text(details.get('intro', ''))
        msg += f"简介: {intro[:150]}...\n"
        
        # 试读
        if details.get('first_chapter_title'):
            msg += f"\n【试读】{details['first_chapter_title']}\n"
            content = self._clean_text(details.get('first_chapter_content', ''))
            msg += f"{content[:180]}...\n"
        
        # 转换回 PC 链接方便查看
        pc_url = details['url'].replace('m.qidian.com', 'www.qidian.com')
        msg += f"\n链接: {pc_url}"

        chain.append(Comp.Plain(msg))
        return chain

    def _clean_text(self, text):
        """文本排版优化：处理换行、多余标签和实体字符"""
        if not text: return ""
        # 转换段落标签为换行
        text = re.sub(r'</?p>|<br\s*/?>', '\n', text)
        # 移除残余 HTML
        text = re.sub(r'<[^>]+>', '', text)
        # 处理常见转义
        text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
        # 合并多余空行并修剪
        return re.sub(r'\n+', '\n', text).strip()

    async def terminate(self):
        logger.info("网文信息查询插件已卸载")