"""A shim of shtab."""
from argparse import Action
from argparse import ArgumentParser
from typing import Any
from typing import Dict
from typing import List

FILE = None
DIRECTORY = DIR = None


def add_argument_to(parser: ArgumentParser, *args: List[Any], **kwargs: Dict[str, Any]):
    Action.complete = None  # type: ignore
    return parser
