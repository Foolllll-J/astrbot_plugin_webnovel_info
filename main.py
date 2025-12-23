import asyncio
import aiohttp
import base64
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
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
        # Initialize source manager
        self.source_manager = SourceManager()
        self.config = config or {}
        # 将中文配置值转换为英文内部值
        display_mode_config = self.config.get("display_mode", "详细")
        self.display_mode = "detailed" if display_mode_config == "详细" else "concise"

        # 用户搜索状态管理
        self.user_search_state = {}

        logger.info("网文信息查询插件初始化")

    def _get_user_search_state(self, user_id: str):
        """获取用户搜索状态"""
        if user_id not in self.user_search_state:
            self.user_search_state[user_id] = {
                "keyword": "",
                "current_page": 1,
                "max_pages": 1,
                "search_type": "qidian",  # 搜索类型
                "results": []  # 保存当前页的搜索结果
            }
        return self.user_search_state[user_id]

    def _update_user_search_state(self, user_id: str, keyword: str, current_page: int, max_pages: int, search_type: str, results: list = None):
        """更新用户搜索状态"""
        state = self._get_user_search_state(user_id)
        state["keyword"] = keyword
        state["current_page"] = current_page
        state["max_pages"] = max_pages
        state["search_type"] = search_type
        if results is not None:
            state["results"] = results

    @filter.command("起点")
    async def qidian_search(self, event: AstrMessageEvent):
        """Search for books on Qidian with pagination"""
        command_text = event.message_str.strip()
        command_parts = command_text.split()

        if len(command_parts) < 2:
            yield event.plain_result("请输入书名。用法: /起点 <书名>")
            return

        # 检查是否有数字参数（用于选择具体书籍）
        args = command_parts[1:]
        book_name = " ".join(args)
        selected_index = None

        # 检查是否是选择序号
        if len(args) == 1 and args[0].isdigit():
            # 如果是纯数字，可能是在选择书籍详情
            user_id = event.get_sender_id()
            state = self._get_user_search_state(user_id)
            if state.get("keyword") and state.get("search_type") == "qidian":
                selected_index = int(args[0])
                book_name = state["keyword"]
            else:
                yield event.plain_result("请先搜索书籍后再选择序号。用法: /起点 <书名>")
                return
        elif len(args) > 1 and args[-1].isdigit():
            # 最后一个参数是数字，表示选择书籍
            selected_index = int(args[-1])
            book_name = " ".join(args[:-1])

        user_id = event.get_sender_id()

        source = self.source_manager.get_source("qidian")
        if not source:
            yield event.plain_result("起点解析器未找到。")
            return

        if selected_index is not None:
            # 获取指定序号的书籍详情
            results = self._get_user_search_state(user_id).get("results", [])
            if not results or selected_index < 1 or selected_index > len(results):
                yield event.plain_result(f"序号 {selected_index} 不存在，请检查搜索结果。")
                return

            selected_book = results[selected_index - 1]
            book_url = selected_book.get("url")
            if not book_url:
                yield event.plain_result("无法获取该书籍的链接。")
                return

            yield event.plain_result(f"正在获取《{selected_book.get('name', 'N/A')}》的详细信息...")

            try:
                details = await source.get_book_details(book_url)
                if not details:
                    yield event.plain_result(f"获取书籍详细信息失败。")
                    return

                # 根据配置显示详细或简洁信息
                chain = await self._format_book_details(details, event)
                yield event.chain_result(chain)

            except Exception as e:
                logger.error(f"获取书籍详情时出错: {e}", exc_info=True)
                yield event.plain_result(f"获取书籍详情时发生错误: {e}")
        else:
            # 搜索书籍 - 获取第一页结果及元数据
            yield event.plain_result(f"正在为《{book_name}》搜索起点...")

            try:
                # 获取第一页搜索结果及元数据
                search_result = await source.search_book(book_name, page=1, return_metadata=True)
                if not search_result or not search_result.get("books"):
                    yield event.plain_result(f"在起点找不到《{book_name}》这本书。")
                    return

                search_results = search_result["books"]
                total_results = search_result["total"]
                is_last_page = search_result["is_last"]

                # 计算总页数 (每页20个结果)
                results_per_page = 20
                max_pages = (total_results + results_per_page - 1) // results_per_page  # 向上取整
                # 如果当前页是最后一页，或者根据记录数判断只有这一页，则设置正确的max_pages
                if is_last_page or len(search_results) < results_per_page:
                    max_pages = 1

                # If only one result and it matches exactly, return details directly
                if len(search_results) == 1:
                    first_result = search_results[0]
                    if first_result.get("name", "").lower() == book_name.lower():
                        book_url = first_result.get("url")
                        if book_url:
                            yield event.plain_result(f"找到唯一匹配结果，正在获取详细信息...")

                            details = await source.get_book_details(book_url)
                            if not details:
                                yield event.plain_result(f"获取《{book_name}》的详细信息失败。")
                                return

                            # 根据配置显示详细或简洁信息
                            chain = await self._format_book_details(details, event)
                            yield event.chain_result(chain)
                            return

                # Show first page of search results
                current_page_results = search_results

                # Update user search state
                self._update_user_search_state(user_id, book_name, 1, max_pages, "qidian", current_page_results)

                # Display search results list
                message_text = f"以下是【{book_name}】的第 1/{max_pages} 页搜索结果 (共{total_results}个结果):\n"
                for i, book in enumerate(current_page_results):
                    num = i + 1
                    name = book.get("name", "未知书籍")
                    author = book.get("author", "未知作者")
                    message_text += f"{num}. {name}\n    作者：{author}\n"

                message_text += f"\n💡 请使用 `/起点 <序号>` 或 `/qd <序号>` 查看详情"
                if max_pages > 1:
                    message_text += f"\n💡 使用 /起点 下一页 或 /qd 下一页 翻页"

                yield event.plain_result(message_text)

            except Exception as e:
                logger.error(f"搜索起点时出错: {e}", exc_info=True)
                yield event.plain_result(f"搜索过程中发生错误: {e}")

    @filter.command("qd")
    async def qidian_search_alias(self, event: AstrMessageEvent):
        """起点命令的别名"""
        # 调用 qidian_search 方法
        async for result in self.qidian_search(event):
            yield result

    @filter.command_group("qd")
    def qd_group(self):
        """起点搜索命令组"""
        pass

    @filter.command_group("起点")
    def qidian_group(self):
        """起点搜索命令组"""
        pass

    @qd_group.command("下一页")
    async def qidian_next_page_alias(self, event: AstrMessageEvent):
        """下一页 - qd别名"""
        # 调用通用的下一页方法
        async for result in self.qidian_next_page_common(event):
            yield result

    @qd_group.command("上一页")
    async def qidian_prev_page_alias(self, event: AstrMessageEvent):
        """上一页 - qd别名"""
        # 调用通用的上一页方法
        async for result in self.qidian_prev_page_common(event):
            yield result

    @qidian_group.command("下一页")
    async def qidian_next_page(self, event: AstrMessageEvent):
        """下一页 - 起点命令"""
        # 调用通用的下一页方法
        async for result in self.qidian_next_page_common(event):
            yield result

    @qidian_group.command("上一页")
    async def qidian_prev_page(self, event: AstrMessageEvent):
        """上一页 - 起点命令"""
        # 调用通用的上一页方法
        async for result in self.qidian_prev_page_common(event):
            yield result

    async def qidian_next_page_common(self, event: AstrMessageEvent):
        """通用下一页方法"""
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)

        if not state.get("keyword") or state.get("search_type") != "qidian":
            yield event.plain_result("🤔 没有可供翻页的搜索结果，请先使用 /起点 <书名> 进行搜索。")
            return

        current_page = state.get("current_page", 1)
        keyword = state["keyword"]

        source = self.source_manager.get_source("qidian")
        if not source:
            yield event.plain_result("起点解析器未找到。")
            return

        try:
            # Fetch the next page of results with metadata
            next_page = current_page + 1
            search_result = await source.search_book(keyword, page=next_page, return_metadata=True)

            if not search_result or not search_result.get("books"):
                yield event.plain_result("➡️ 已经是最后一页了。")
                return

            search_results = search_result["books"]
            total_results = search_result["total"]
            is_last_page = search_result["is_last"]

            # Calculate max_pages based on total results
            results_per_page = 20
            max_pages = (total_results + results_per_page - 1) // results_per_page  # 向上取整

            # Update user search state with the new page
            self._update_user_search_state(user_id, keyword, next_page, max_pages, "qidian", search_results)

            message_text = f"以下是【{keyword}】的第 {next_page}/{max_pages} 页搜索结果 (共{total_results}个结果):\n"
            for i, book in enumerate(search_results):
                num = (next_page - 1) * 20 + i + 1  # Calculate global index
                name = book.get("name", "未知书籍")
                author = book.get("author", "未知作者")
                message_text += f"{num}. {name}\n    作者：{author}\n"

            message_text += f"\n💡 请使用 `/起点 <序号>` 或 `/qd <序号>` 查看详情"
            if next_page < max_pages:
                message_text += f"\n💡 使用 /起点 下一页 或 /qd 下一页 翻页"

            yield event.plain_result(message_text)
        except Exception as e:
            logger.error(f"翻页失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 翻页时发生错误: {str(e)}")

    async def qidian_prev_page_common(self, event: AstrMessageEvent):
        """通用上一页方法"""
        user_id = event.get_sender_id()
        state = self._get_user_search_state(user_id)

        if not state.get("keyword") or state.get("search_type") != "qidian":
            yield event.plain_result("🤔 没有可供翻页的搜索结果，请先使用 /起点 <书名> 进行搜索。")
            return

        current_page = state.get("current_page", 1)

        if current_page <= 1:
            yield event.plain_result("⬅️ 已经是第一页了。")
            return

        prev_page = current_page - 1
        keyword = state["keyword"]

        source = self.source_manager.get_source("qidian")
        if not source:
            yield event.plain_result("起点解析器未找到。")
            return

        try:
            # Fetch the previous page of results with metadata
            search_result = await source.search_book(keyword, page=prev_page, return_metadata=True)
            if not search_result or not search_result.get("books"):
                yield event.plain_result(f"😢 无法加载第 {prev_page} 页。")
                return

            search_results = search_result["books"]
            total_results = search_result["total"]
            is_last_page = search_result["is_last"]

            # Calculate max_pages based on total results
            results_per_page = 20
            max_pages = (total_results + results_per_page - 1) // results_per_page  # 向上取整

            # Update user search state with the previous page
            self._update_user_search_state(user_id, keyword, prev_page, max_pages, "qidian", search_results)

            message_text = f"以下是【{keyword}】的第 {prev_page}/{max_pages} 页搜索结果 (共{total_results}个结果):\n"
            for i, book in enumerate(search_results):
                num = (prev_page - 1) * 20 + i + 1  # Calculate global index
                name = book.get("name", "未知书籍")
                author = book.get("author", "未知作者")
                message_text += f"{num}. {name}\n    作者：{author}\n"

            message_text += f"\n💡 请使用 `/起点 <序号>` 或 `/qd <序号>` 查看详情"
            if prev_page > 1:
                message_text += f"\n💡 使用 /起点 上一页 翻页"
            if prev_page < max_pages:
                message_text += f"\n💡 使用 /qd 下一页 翻页"

            yield event.plain_result(message_text)
        except Exception as e:
            logger.error(f"翻页失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 翻页时发生错误: {str(e)}")

    async def _format_book_details(self, details, event):
        """根据配置格式化书籍详情"""
        chain = []
        message_text = ""

        # 添加封面图片（如果可用）
        if details.get("cover"):
            cover_url = details["cover"]
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(cover_url, timeout=10) as img_response:
                        img_response.raise_for_status()
                        image_bytes = await img_response.read()
                image_base64 = base64.b64encode(image_bytes).decode()
                image_component = Comp.Image(file=f"base64://{image_base64}")
                chain.append(image_component)
                logger.info("封面图片加载成功")
            except Exception as e:
                logger.warning(f"下载封面图片失败: {e}")
                message_text += "🖼️ 封面加载失败\n"

        # 根据显示模式格式化信息
        if self.display_mode == "concise":
            # 简洁模式：封面、书名、作者、字数、简介
            if details.get("name"):
                message_text += f"---【{details['name']}】---\n"

            if details.get("author"):
                message_text += f"作者: {details['author']}\n"

            if details.get("word_count") and str(details.get("word_count")) not in ["", "0", "N/A", "None"]:
                message_text += f"字数: {details['word_count']}\n"

            if details.get("intro"):
                intro = self._clean_text(details["intro"])
                if intro not in ["", "N/A", "None"]:
                    message_text += f"简介: {intro[:200]}...\n"
        else:
            # 详细模式：显示所有可用信息
            if details.get("name"):
                message_text += f"---【{details['name']}】---\n"

            if details.get("author"):
                message_text += f"作者: {details['author']}\n"

            if details.get("category") and str(details.get("category")) not in ["", "N/A", "None"]:
                message_text += f"类型: {details['category']}\n"

            status = details.get('status')
            word_count = details.get('word_count')
            total_chapters = details.get('total_chapters')
            if status and str(status) not in ["", "N/A", "None"]:
                status_info = f"状态: {status}"
                if word_count and str(word_count) not in ["", "0", "N/A", "None"]:
                    status_info += f" | 累计 {word_count}"
                if total_chapters and str(total_chapters) not in ["", "0", "N/A", "None"]:
                    status_info += f" | 共 {total_chapters} 章"
                message_text += f"{status_info}\n"
            elif word_count and str(word_count) not in ["", "0", "N/A", "None"]:
                message_text += f"累计: {word_count}\n"
                if total_chapters and str(total_chapters) not in ["", "0", "N/A", "None"]:
                    message_text += f"共 {total_chapters} 章\n"
            elif total_chapters and str(total_chapters) not in ["", "0", "N/A", "None"]:
                message_text += f"共 {total_chapters} 章\n"

            rating = details.get('rating')
            rating_users = details.get('rating_users')
            if rating and str(rating) not in ["", "N/A", "None", "暂无"]:
                rating_info = f"评分: {rating}"
                if rating_users and str(rating_users) not in ["", "0", "N/A", "None"]:
                    rating_info += f" ({rating_users} 人评价)"
                message_text += f"{rating_info}\n"

            tags = details.get('tags', [])
            if tags and len([tag for tag in tags if tag and str(tag) not in ["", "N/A", "None"]]) > 0:
                valid_tags = [tag for tag in tags if tag and str(tag) not in ["", "N/A", "None"]]
                if valid_tags:
                    message_text += f"标签: {' / '.join(valid_tags)}\n"

            collection = details.get('collection')
            all_recommend = details.get('all_recommend')
            if collection and str(collection) not in ["", "0", "N/A", "None"]:
                heat_info = f"收藏数: {collection}"
                if all_recommend and str(all_recommend) not in ["", "0", "N/A", "None"]:
                    heat_info += f" | 总推荐票: {all_recommend}"
                message_text += f"热度: {heat_info}\n"
            elif all_recommend and str(all_recommend) not in ["", "0", "N/A", "None"]:
                message_text += f"热度: 总推荐票: {all_recommend}\n"

            intro = details.get('intro')
            if intro and str(intro) not in ["", "N/A", "None"]:
                intro_clean = self._clean_text(intro)
                message_text += f"简介: {intro_clean[:200]}...\n"

            last_chapter = details.get('last_chapter')
            last_update = details.get('last_update')
            if last_update and str(last_update) not in ["", "N/A", "None"]:
                update_info = f"最近更新: {last_update}"
                if last_chapter and str(last_chapter) not in ["", "N/A", "None"]:
                    update_info += f" -> {last_chapter}"
                message_text += f"{update_info}\n"
            elif last_chapter and str(last_chapter) not in ["", "N/A", "None"]:
                message_text += f"最新章节: {last_chapter}\n"

        if details.get("url"):
            # 将移动端链接转换为PC端链接 for display
            display_url = details['url'].replace("m.qidian.com", "www.qidian.com")
            message_text += f"链接: {display_url}\n"

        chain.append(Comp.Plain(message_text))
        return chain

    def _clean_text(self, text):
        """清理文本，移除HTML标签等"""
        if not text:
            return ""
        import re
        # 移除HTML标签
        clean_text = re.sub(r'<[^>]+>', '', text)
        # 处理换行符
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        return clean_text

    async def terminate(self):
        logger.info("网文信息查询插件已卸载")