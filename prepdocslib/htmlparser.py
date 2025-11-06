import logging
import re
from typing import IO, Generator
from bs4 import BeautifulSoup
from .page import Page
from .parser import Parser

logger = logging.getLogger("scripts")


def cleanup_data(data: str) -> str:
    """Cleans up the given content using regexes"""
    output = re.sub(r"\n{2,}", "\n", data)
    output = re.sub(r"[^\S\n]{2,}", " ", output)
    output = re.sub(r"-{2,}", "--", output)
    return output.strip()


class LocalHTMLParser(Parser):
    """Parses HTML text into Page objects."""

    def parse(self, content: IO) -> Generator[Page, None, None]:
        logger.info("Extracting text from '%s' using BeautifulSoup", content.name)

        data = content.read()
        soup = BeautifulSoup(data, "html.parser")
        result = soup.get_text()
        yield Page(0, 0, text=cleanup_data(result))
