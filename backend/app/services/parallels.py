"""Read a card's colour / print-run version ("parallel") out of an eBay listing title.

Sellers write these in free text ("Red Orbital RPA /99", "Green Mojo /99 Auto"), so this is a best guess:
the first colour word plus a print run written as "/NN". Titles with neither are "Base".
"""
import re

# Longest first, so "lava flow" is not read as just "lava"
_COLORS = ["neon green", "lava flow", "rainbow", "camo", "black", "blue", "red", "green", "gold", "orange", "purple",
           "pink", "silver", "white", "yellow", "bronze", "teal", "aqua", "emerald", "ruby", "sapphire", "magenta",
           "ice", "copper", "platinum", "lime", "navy"]
_COLOR_RX = re.compile(r"\b(" + "|".join(_COLORS) + r")\b", re.I)
_RUN_RX = re.compile(r"(?<![\w/])/\s?(\d{1,4})\b")
BASE = "Base"


def parse_parallel(title: str) -> str:
    color = _COLOR_RX.search(title)
    run = _RUN_RX.search(title)
    parts = []
    if color:
        parts.append(color.group(1).title())
    if run:
        parts.append(f"/{int(run.group(1))}")
    return " ".join(parts) or BASE
