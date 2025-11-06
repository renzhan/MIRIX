from abc import ABC
from typing import IO, Generator

from .page import Page


class Parser(ABC):
    """
    Abstract parser that parses content into Page objects
    """

    def parse(self, content: IO) -> Generator[Page, None, None]:
        if False:
            yield  # pragma: no cover - this is necessary for mypy to type check
