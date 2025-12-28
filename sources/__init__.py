from .qidian_source import QidianSource
from .ciweimao_source import CiweimaoSource
from .tomato_source import TomatoSource

class SourceManager:
    def __init__(self):
        self.sources = {
            "qidian": QidianSource(),
            "ciweimao": CiweimaoSource(),
            "tomato": TomatoSource(),
        }
    
    def get_source(self, source_name: str):
        return self.sources.get(source_name)