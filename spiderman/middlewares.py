# Define here the models for your spider middleware
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/spider-middleware.html

# useful for handling different item types with a single interface
import logging
import os
import os.path
from pathlib import Path
import pickle

import scrapy
from scrapy.downloadermiddlewares.cookies import CookiesMiddleware


class PersistenceCookiesMiddleware(CookiesMiddleware):
    def __init__(self, debug=False):
        super().__init__(debug)
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def process_request(self, request, spider):
        self.load(spider)
        return super().process_request(request, spider)

    def process_response(self, request, response, spider):
        res = super().process_response(
            request, response, spider
        )
        self.save(spider)
        return res
    
    @staticmethod
    def get_enabled_persistence(spider: scrapy.Spider):
        return spider.settings.get("COOKIES_PERSISTENCE", False)

    @staticmethod
    def get_cookies_persistence_path(spider: scrapy.Spider):
        return Path(spider.settings.get("COOKIES_PERSISTENCE_DIR", ".cookies")).joinpath(f"{spider.name}.cookies")

    def save(self, spider):
        if not self.get_enabled_persistence(spider):
            return
        if self.debug:
            self.logger.debug("Saving cookies to disk for reuse")
        filename = self.get_cookies_persistence_path(spider)
        if not os.path.exists(os.path.dirname(filename)):
            os.makedirs(os.path.dirname(filename))
        with open(filename, "wb") as f:
            pickle.dump(self.jars, f)
            f.flush()

    def load(self, spider):
        if not self.get_enabled_persistence(spider):
            return
        filename = self.get_cookies_persistence_path(spider)
        if self.debug:
            self.logger.debug(f"Trying to load cookies from file '{filename}'")
        if not os.path.exists(filename):
            self.logger.info(f"File '{filename}' for cookie reload doesn't exist")
            logging.info(f"File '{filename}' for cookie reload doesn't exist")
            return
        if not os.path.isfile(filename):
            raise Exception(f"File '{filename}' is not a regular file")

        with open(filename, "rb") as f:
            self.jars = pickle.load(f)
