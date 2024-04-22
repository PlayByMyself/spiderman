# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html


from dataclasses import dataclass, field
from typing import List, Optional
from scrapy.loader import ItemLoader
from itemloaders.processors import MapCompose, TakeFirst, Compose, Identity

def remove_first_and_last_parentheses(value: str) -> str:
    if value and value.startswith("(") and value.endswith(")"):
        return value[1:-1]
    return value


@dataclass
class ComicChapterItem:
    name: Optional[str] = field(default=None)
    size: Optional[str] = field(default=None)
    url: Optional[str] = field(default=None)
    download_url: Optional[str] = field(default=None)
    save_path: Optional[str] = field(default=None)

    @staticmethod
    def filter_chapters(chapters: List['ComicChapterItem']) -> List['ComicChapterItem']:
        return list(filter(lambda x: x.name and x.download_url, chapters))

@dataclass
class ComicItem:
    name: Optional[str] = field(default=None)
    name_en: Optional[str] = field(default=None)
    url: Optional[str] = field(default=None)
    chapters: List[ComicChapterItem] = field(default_factory=list)

class ComicChapterLoader(ItemLoader):
    default_item_class = ComicChapterItem
    default_input_processor = MapCompose(str.strip)
    default_output_processor = TakeFirst()

class ComicLoader(ItemLoader):
    default_item_class = ComicItem
    default_output_processor = TakeFirst()

    name_in = MapCompose(str.strip)
    name_en_in = MapCompose(str.strip, remove_first_and_last_parentheses)
    chapters_in = Compose(ComicChapterItem.filter_chapters)
    chapters_out = Identity()

