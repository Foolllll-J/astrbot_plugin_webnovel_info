from abc import ABC, abstractmethod


class BaseSource(ABC):
    """Base class for all novel sources"""
    
    @abstractmethod
    async def search_book(self, keyword: str):
        """Search for books by keyword"""
        pass
    
    @abstractmethod
    async def get_book_details(self, book_url: str):
        """Get detailed information for a specific book"""
        pass