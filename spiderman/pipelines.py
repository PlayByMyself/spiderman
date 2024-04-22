# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface
from datetime import datetime, timedelta
import io
import logging
import os
import pickle
from typing import DefaultDict, Literal, Optional
import aiofiles
import aiofiles.os
import aiofiles.ospath
from httpx import AsyncClient
import httpx
import scrapy
import urllib3
import urllib3.util
from spiderman.items import ComicChapterItem, ComicItem
from scrapy.http.cookies import CookieJar
from tenacity import RetryCallState, retry, retry_if_exception_type, wait_fixed

from spiderman.middlewares import PersistenceCookiesMiddleware

logger = logging.getLogger(__name__)

def after_retry_log(retry_state: RetryCallState):
    logger.warning(f"Retrying download of chapter '{retry_state.args[0].get_chapter_full_name(retry_state.args[1], retry_state.args[2])}' after {retry_state.attempt_number} attempts (last exception: {retry_state.outcome.exception() if retry_state.outcome else ''})")

class ComicDownloadPipeline:
    async def process_item(self, item, spider):
        if not item or not isinstance(item, ComicItem):
            return item
        if not item.chapters:
            return item
        for chapter in item.chapters:
            # try:
            await self.download_chapter(item, chapter, spider)
            # except Exception as e:
            #     logger.error(f"Error downloading chapter '{self.get_chapter_full_name(item, chapter)}': {e}")
        return item

    @retry(
        retry=retry_if_exception_type(httpx.RemoteProtocolError),
        wait=wait_fixed(timedelta(seconds=30)),
        reraise=True,
        after=after_retry_log,
    )
    async def download_chapter(
        self, item: ComicItem, chapter: ComicChapterItem, spider: scrapy.Spider
    ):
        if not chapter or not isinstance(chapter, ComicChapterItem):
            return
        if not chapter.download_url:
            logger.warning(
                f"Chapter '{self.get_chapter_full_name(item, chapter)}' has no download URL"
            )
            return
        if not chapter.save_path:
            logger.warning(
                f"Chapter '{self.get_chapter_full_name(item, chapter)}' has no save path"
            )
            return
        if await aiofiles.ospath.exists(chapter.save_path):
            logger.info(
                f"Chapter '{self.get_chapter_full_name(item, chapter)}' already exists, skipping"
            )
            return
        if not await aiofiles.ospath.exists(os.path.dirname(chapter.save_path)):
            await aiofiles.os.makedirs(os.path.dirname(chapter.save_path))
        cookie_jar = await self.load_cookies(chapter.download_url, spider)
        download_size = 0
        temp_downloading_file = f"{chapter.save_path}.downloading"
        headers = {
            "User-Agent": spider.settings.get("USER_AGENT"),
            # "If-Range": "Wed, 15 Nov 1995 04:58:08 GMT",
        }
        async with AsyncClient(
            proxy=os.getenv("HTTP_PROXY"), timeout=None
        ) as http_client:
            if await aiofiles.ospath.exists(temp_downloading_file):
                async with aiofiles.open(temp_downloading_file, "rb") as f:
                    await f.seek(0, os.SEEK_END)
                    download_size = await f.tell()
                    if download_size > 0:
                        logger.info(
                            f"Resuming download of chapter '{self.get_chapter_full_name(item, chapter)}' from {self.download_size_str(download_size)} bytes"
                        )
                        headers.update(
                            {
                                "Range": f"bytes={download_size}-",
                            }
                        )
            async with http_client.stream(
                "GET",
                chapter.download_url,
                follow_redirects=True,
                cookies=cookie_jar.jar,
                headers=headers,
                timeout=None,
            ) as response:
                if not response.is_success:
                    logger.error(
                        f"Failed to download chapter '{self.get_chapter_full_name(item, chapter)}'"
                    )
                    return
                file_size = (
                    int(response.headers.get("Content-Length"))
                    if response.headers.get("Content-Length")
                    else 0
                )
                content_range = response.headers.get("Content-Range")
                open_mode: Literal["ab", "wb"] = "ab"
                if response.status_code == 206 and content_range:
                    file_size = max(file_size, int(content_range.split("/")[-1]))
                    logger.debug(
                        f"Chapter '{self.get_chapter_full_name(item, chapter)}' download from range '{content_range}'"
                    )
                elif response.status_code == 200:
                    open_mode = "wb"
                    logger.debug(
                        f"Chapter '{self.get_chapter_full_name(item, chapter)}' download from start"
                    )
                else:
                    logger.error(
                        f"Failed to download chapter '{self.get_chapter_full_name(item, chapter)}'"
                    )
                    return
                async with aiofiles.open(temp_downloading_file, open_mode) as f:
                    last_time = datetime.now()
                    async for chunk in response.aiter_bytes(1024):
                        await f.write(chunk)
                        download_size = download_size + len(chunk)
                        if (datetime.now() - last_time).total_seconds() >= 5:
                            last_time = datetime.now()
                            logger.debug(
                                f"Downloading chapter '{self.get_chapter_full_name(item, chapter)} ({self.download_size_str(download_size)}/{self.download_size_str(file_size) or chapter.size or 'unknown'})'"
                            )
                if download_size != file_size:
                    logger.warning(
                        f"Chapter '{self.get_chapter_full_name(item, chapter)}' downloaded size ({self.download_size_str(download_size)}) doesn't match expected size ({self.download_size_str(file_size)})"
                    )
                    return
                await aiofiles.os.rename(temp_downloading_file, chapter.save_path)

    def download_size_str(self, size: Optional[int]) -> str:
        if size is None:
            return ""
        if size < 1024:
            return f"{size} bytes"
        if size < 1024 * 1024:
            return f"{size / 1024:.2f} KB"
        return f"{size / 1024 / 1024:.2f} MB"

    async def load_cookies(self, url: str, spider: scrapy.Spider) -> CookieJar:
        if not PersistenceCookiesMiddleware.get_enabled_persistence(spider):
            return CookieJar()
        filename = PersistenceCookiesMiddleware.get_cookies_persistence_path(spider)
        if not await aiofiles.os.path.exists(filename):
            logger.info(f"File '{filename}' for cookie reload doesn't exist")
            return CookieJar()
        logger.info(f"Loading cookies from file '{filename}'")
        hostname = urllib3.util.parse_url(url).hostname or ""
        async with aiofiles.open(filename, "rb") as f:
            data = await f.read()
            with io.BytesIO() as b:
                b.write(data)
                b.seek(0)
                jars: DefaultDict[str, CookieJar] = pickle.load(b)
                jar = jars.get(hostname, CookieJar())
                logger.info(f"Loaded {len(jar.jar)} cookies for '{hostname}'")
                return jar

    def get_chapter_full_name(self, item: ComicItem, chapter: ComicChapterItem) -> str:
        return f"{item.name} - {chapter.name}"
