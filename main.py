import aiohttp
import base64
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

from .parsers.qidian import QidianParser

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
        self.parsers = {
            "qidian": QidianParser()
        }
        logger.info("网文信息查询插件初始化")

    @filter.command("起点")
    async def qidian_search(self, event: AstrMessageEvent, book_name: str):
        if not book_name:
            yield event.plain_result("请输入书名。用法: /起点 <书名>")
            return

        parser = self.parsers.get("qidian")
        if not parser:
            yield event.plain_result("起点解析器未找到。")
            return

        yield event.plain_result(f"正在为《{book_name}》搜索起点...")

        try:
            search_results = await parser.search_book(book_name)
            if not search_results:
                yield event.plain_result(f"在起点找不到《{book_name}》这本书。")
                return

            # Take the first result
            first_result_url = search_results[0].get("url")
            if not first_result_url:
                yield event.plain_result("搜索结果中没有找到书籍链接。")
                return

            details = await parser.get_book_details(first_result_url)

            if not details:
                yield event.plain_result(f"获取《{book_name}》的详细信息失败。")
                return

            # Format the message
            chain = []
            message_text = ""

            if details.get("cover_url"):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(details["cover_url"]) as img_response:
                            img_response.raise_for_status()
                            image_bytes = await img_response.read()
                    image_base64 = base64.b64encode(image_bytes).decode()
                    image_component = Comp.Image(file=f"base64://{image_base64}")
                    chain.append(image_component)
                except Exception as e:
                    logger.warning(f"下载封面图片失败: {e}")
                    message_text += "🖼️ 封面加载失败\n"

            message_text += f"---【{details.get('name', 'N/A')}】---\n"
            message_text += f"作者: {details.get('author', 'N/A')}\n"
            
            tags = details.get('tags')
            if tags:
                message_text += f"标签: {' '.join(tags)}\n"

            message_text += f"最新章节: {details.get('last_chapter', 'N/A')}\n"
            message_text += f"简介: {details.get('intro', 'N/A')}\n"
            message_text += f"链接: {details.get('url', 'N/A')}\n"
            
            chain.append(Comp.Plain(message_text))
            yield event.chain_result(chain)

        except Exception as e:
            logger.error(f"搜索起点时出错: {e}", exc_info=True)
            yield event.plain_result(f"搜索过程中发生错误: {e}")

    async def terminate(self):
        logger.info("网文信息查询插件已卸载")