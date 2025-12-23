from .qidian_source import QidianSource


class SourceManager:
    """Manages all available sources"""
    
    def __init__(self):
        self.sources = {
            "qidian": QidianSource(),
            # Add more sources here as they are implemented
            # "zongheng": ZonghengSource(),
            # "chuangshi": ChuangshiSource(),
        }
    
    def get_source(self, source_name: str):
        """Get a source by name"""
        return self.sources.get(source_name)
    
    def get_all_sources(self):
        """Get all available sources"""
        return self.sources