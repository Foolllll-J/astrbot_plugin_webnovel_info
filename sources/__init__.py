from .qidian_source import QidianSource
from .ciweimao_source import CiweimaoSource
from .tomato_source import TomatoSource
from .sfacg_source import SfacgSource
from .faloo_source import FalooSource
from .qimao_source import QiMaoSource

class SourceManager:
    def __init__(self):
        self.sources = {
            "qidian": QidianSource(),
            "ciweimao": CiweimaoSource(),
            "tomato": TomatoSource(),
            "sfacg": SfacgSource(),
            "faloo": FalooSource(),
            "qimao": QiMaoSource(),
        }
    
    def get_source(self, source_name: str):
        return self.sources.get(source_name)