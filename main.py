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
    "1.0.0",
    ""
)
class WebnovelInfoPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        # Initialize source manager
        self.source_manager = SourceManager()
        logger.info("网文信息查询插件初始化")

    @filter.command("起点")
    async def qidian_search(self, event: AstrMessageEvent, book_name: str):
        """Search for books on Qidian"""
        if not book_name:
            yield event.plain_result("请输入书名。用法: /起点 <书名>")
            return

        source = self.source_manager.get_source("qidian")
        if not source:
            yield event.plain_result("起点解析器未找到。")
            return

        yield event.plain_result(f"正在为《{book_name}》搜索起点...")

        try:
            search_results = await source.search_book(book_name)
            if not search_results:
                yield event.plain_result(f"在起点找不到《{book_name}》这本书。")
                return

            # Take the first result
            first_result = search_results[0]
            book_url = first_result.get("url")
            if not book_url:
                yield event.plain_result("搜索结果中没有找到书籍链接。")
                return

            details = await source.get_book_details(book_url)
            if not details:
                yield event.plain_result(f"获取《{book_name}》的详细信息失败。")
                return

            # Format the message
            chain = []
            message_text = ""

            # Add cover image if available
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

            # Format the book details
            message_text += f"---【{details.get('name', 'N/A')}】---\n"
            message_text += f"作者: {details.get('author', 'N/A')}\n"

            category = details.get('category', 'N/A')
            if category != 'N/A':
                message_text += f"类型: {category}\n"

            status = details.get('status', 'N/A')
            word_count = details.get('word_count', 'N/A')
            total_chapters = details.get('total_chapters', 'N/A')
            if status != 'N/A' or word_count != 'N/A' or total_chapters != 'N/A':
                status_info = f"状态: {status}" if status != 'N/A' else ""
                if word_count != 'N/A':
                    status_info += f" | 累计 {word_count}"
                if total_chapters != 'N/A':
                    status_info += f" | 共 {total_chapters} 章"
                message_text += f"{status_info}\n"

            rating = details.get('rating', 'N/A')
            rating_users = details.get('rating_users', 'N/A')
            if rating != 'N/A':
                rating_info = f"评分: {rating}"
                if rating_users != 'N/A':
                    rating_info += f" ({rating_users} 人评价)"
                message_text += f"{rating_info}\n"

            tags = details.get('tags', [])
            if tags:
                message_text += f"标签: {' / '.join(tags)}\n"

            collection = details.get('collection', 'N/A')
            all_recommend = details.get('all_recommend', 'N/A')
            if collection != 'N/A' or all_recommend != 'N/A':
                heat_info = "热度: "
                if collection != 'N/A':
                    heat_info += f"收藏数: {collection}"
                if all_recommend != 'N/A':
                    heat_info += f" | 总推荐票: {all_recommend}"
                message_text += f"{heat_info}\n"

            intro = details.get('intro', 'N/A')
            if intro != 'N/A':
                # Clean and truncate intro
                intro_clean = self._clean_text(intro)
                message_text += f"简介: {intro_clean[:200]}...\n"

            last_chapter = details.get('last_chapter', 'N/A')
            last_update = details.get('last_update', 'N/A')
            if last_chapter != 'N/A' or last_update != 'N/A':
                update_info = "最近更新: "
                if last_update != 'N/A':
                    update_info += f"{last_update} -> "
                if last_chapter != 'N/A':
                    update_info += f"{last_chapter}"
                message_text += f"{update_info}\n"

            message_text += f"链接: {details.get('url', 'N/A')}\n"

            chain.append(Comp.Plain(message_text))
            yield event.chain_result(chain)

        except Exception as e:
            logger.error(f"搜索起点时出错: {e}", exc_info=True)
            yield event.plain_result(f"搜索过程中发生错误: {e}")

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