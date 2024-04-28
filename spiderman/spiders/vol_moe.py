import os
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
import scrapy
from scrapy.http import HtmlResponse
from scrapy.utils.reactor import install_reactor
from playwright.async_api import Page
import urllib3
import urllib3.util
from spiderman.items import ComicChapterLoader, ComicLoader
from dotenv import load_dotenv
from itemloaders.processors import MapCompose

load_dotenv()

install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")


def should_abort_request(request):
    return (
        request.resource_type == "image"
        or ".jpg" in request.url
        or ".png" in request.url
    )


class VolMoeSpider(scrapy.Spider):
    name = "vol.moe"

    custom_settings = {
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy.downloadermiddlewares.cookies.CookiesMiddleware": None,
            "spiderman.middlewares.PersistenceCookiesMiddleware": 700,
        },
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": True,
        },
        "PLAYWRIGHT_ABORT_REQUEST": should_abort_request,
        "COOKIES_ENABLED": True,
        "COOKIES_PERSISTENCE": True,
        "COOKIES_DEBUG": True,
        "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
        "ITEM_PIPELINES": {"spiderman.pipelines.ComicDownloadPipeline": 1},
    }

    def __init__(
        self,
        name: Optional[str] = None,
        follow_list_xpath: Optional[str] = None,
        proxy: Optional[str] = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY"),
        download_dir: Optional[str] = os.getenv("VOL_MOE_DOWNLOAD_DIR"),
        user_name: Optional[str] = os.getenv("VOL_MOE_USER_NAME"),
        password: Optional[str] = os.getenv("VOL_MOE_PASSWORD"),
        **kwargs: Any,
    ):
        assert user_name, "user_name is required"
        assert password, "password is required"
        super().__init__(name, **kwargs)
        self.host = "https://vol.moe"
        self.start_url = f"{self.host}/myfollow.php"
        self.login_url = f"{self.host}/login_do.php"
        self.login_page_url = f"{self.host}/login.php"
        self.follow_list_xpath = (
            follow_list_xpath or f"//td/a[contains(@href, '{self.host}/c/')]/@href"
        )
        self.proxy = proxy
        self.download_dir = download_dir or "./download"
        self.user_name = user_name or None
        self.password = password or None
        self.playwright_meta = {
            "playwright": True,
            "playwright_context": self.__class__.__name__,
            "playwright_include_page": True,
            "playwright_context_kwargs": {
                "java_script_enabled": True,
                "ignore_https_errors": True,
                "proxy": {
                    "server": self.proxy or None,
                },
            },
        }

    def start_requests(self) -> Iterable[scrapy.Request]:
        urls = [self.start_url]
        for i, url in enumerate(urls):
            yield scrapy.Request(
                url=url,
                callback=self._callback(self.parse_myfollow, url),
                meta={
                    "proxy": self.proxy or None,
                    "cookiejar": urllib3.util.parse_url(url).hostname,
                },
            )

    def _callback(self, source_callback: Callable, source_url: str) -> Callable:
        async def wrapper(response: HtmlResponse) -> Any:
            if response.url == self.login_page_url:
                return scrapy.FormRequest.from_response(
                    response=response,
                    url=self.login_page_url,
                    callback=self._parse_login_page,
                    meta={
                        "source_url": source_url,
                        "source_callback": source_callback,
                        "proxy": self.proxy or None,
                        "cookiejar": response.meta["cookiejar"],
                    },
                    dont_filter=True,
                )
            else:
                return source_callback(response)

        return wrapper

    async def _parse_login_page(self, response: HtmlResponse) -> Any:
        payload = f"email={self.user_name}&passwd={self.password}&keepalive=on"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        yield scrapy.Request(
            url=self.login_url,
            body=payload,
            method="POST",
            callback=self._after_login,
            headers=headers,
            meta={
                "source_url": response.meta["source_url"],
                "source_callback": response.meta["source_callback"],
                "proxy": self.proxy or None,
                "cookiejar": response.meta["cookiejar"],
            },
            dont_filter=True,
        )

    async def _after_login(self, response: HtmlResponse) -> Any:
        source_url = response.meta["source_url"]
        callback = response.meta["source_callback"]
        yield scrapy.Request(
            url=source_url,
            callback=callback,
            meta={
                "proxy": self.proxy or None,
                "cookiejar": response.meta["cookiejar"],
            },
            dont_filter=True,
        )

    async def parse_myfollow(self, response: HtmlResponse) -> Any:
        comic_url_list = response.xpath(self.follow_list_xpath).getall()
        count = 0
        for comic_url in comic_url_list:
            yield scrapy.Request(
                url=comic_url,
                callback=self.parse_detail,
                errback=self.close_context_on_error,
                meta={
                    **self.playwright_meta,
                    "cookiejar": response.meta["cookiejar"],
                },
            )
            count = count + 1

    async def close_context_on_error(self, failure):
        page = failure.request.meta["playwright_page"]
        await page.close()
        await page.context.close()

    async def parse_detail(self, response: HtmlResponse) -> Any:
        page: Page = response.meta["playwright_page"]
        await page.wait_for_load_state("networkidle")
        await page.wait_for_load_state("domcontentloaded")
        content = await page.content()
        await page.close()
        new_response = HtmlResponse(
            url=page.url,
            body=content,
            encoding="utf-8",
            request=response.request,
        )
        loder = ComicLoader(response=new_response)
        loder.add_xpath("name", xpath="string(//td[@class='author']/font[1])")
        loder.add_xpath("name_en", xpath="string(//td[@class='author']/font[5])")
        loder.add_value("url", response.url)
        loder.add_value(
            "chapters",
            [
                i
                for i in self.parse_chapters(
                    loder.get_output_value("name"), new_response
                )
            ],
        )
        return loder.load_item()

    def parse_chapters(self, comic_name: str, response: HtmlResponse):
        rows = response.xpath(
            '//*[@id="div_tabdata"][@class="book_list"]/tbody/tr[count(./td) = 5]'
        )
        i = 0
        for row in rows:
            left_chapter_item_loader = ComicChapterLoader(selector=row)
            left_chapter_item_loader.add_xpath(
                "name", xpath='td[1]/b[contains(@title,"製作")]/text()'
            )
            left_chapter_item_loader.add_xpath(
                "size",
                xpath='td[1]/font[contains(., "頁")][@class="filesize"]/text()',
                re=r"(\d+\.\d+M \(\d+頁\))",
            )
            left_chapter_item_loader.add_xpath(
                "download_url",
                'td[2]/a[contains(., "下載")][contains(@onclick,"captcha_show")]/@onclick',
                MapCompose(self.add_host),
                re=r"captcha_show\('(.+?)'\)",
            )
            if left_chapter_item_loader.get_output_value("name"):
                order_prefix = f"[{i}]-"
                left_chapter_item_loader.add_value(
                    "save_path",
                    f"{Path(self.download_dir).joinpath(comic_name).joinpath(order_prefix + left_chapter_item_loader.get_output_value('name')).as_posix()}.epub",
                )
                i = i + 1
            yield left_chapter_item_loader.load_item()
            right_chapter_item_loader = ComicChapterLoader(selector=row)
            right_chapter_item_loader.add_xpath(
                "name", xpath='td[4]/b[contains(@title,"製作")]/text()'
            )
            right_chapter_item_loader.add_xpath(
                "size",
                xpath='td[4]/font[contains(., "頁")][@class="filesize"]/text()',
                re=r"(\d+\.\d+M \(\d+頁\))",
            )
            right_chapter_item_loader.add_xpath(
                "download_url",
                'td[5]/a[contains(., "下載")][contains(@onclick,"captcha_show")]/@onclick',
                MapCompose(self.add_host),
                re=r"captcha_show\('(.+?)'\)",
            )
            if right_chapter_item_loader.get_output_value("name"):
                order_prefix = f"[{i}]-"
                right_chapter_item_loader.add_value(
                    "save_path",
                    f"{Path(self.download_dir).joinpath(comic_name).joinpath(order_prefix + right_chapter_item_loader.get_output_value('name')).as_posix()}.epub",
                )
                i = i + 1
            yield right_chapter_item_loader.load_item()

    def add_host(self, path: str) -> str:
        return f"{self.host}{path}"
