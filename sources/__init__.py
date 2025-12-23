from .qidian_source import QidianSource
from .ciweimao_source import CiweimaoSource

class SourceManager:
    def __init__(self):
        self.sources = {
            "qidian": QidianSource(),
            "ciweimao": CiweimaoSource(),
        }
    
    def get_source(self, source_name: str):
        return self.sources.get(source_name)