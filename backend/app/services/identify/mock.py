import hashlib

from ...models import CardCategory
from .base import CardIdentification, build_search_query

# A spread across categories so the demo exercises mixed inventory.
_SAMPLES: list[dict] = [
    dict(category=CardCategory.sports, name="Ken Griffey Jr.", set_name="Upper Deck", year="1989",
         card_number="1", variant="Rookie", grade="Raw", condition="EX"),
    dict(category=CardCategory.sports, name="Victor Wembanyama", set_name="Panini Prizm", year="2023",
         card_number="136", variant="Silver Prizm", grade="PSA 10"),
    dict(category=CardCategory.sports, name="Tom Brady", set_name="Topps Chrome", year="2000",
         card_number="241", variant="Rookie", grade="Raw", condition="NM"),
    dict(category=CardCategory.pokemon, name="Charizard", set_name="Base Set", year="1999",
         card_number="4/102", variant="Holo Unlimited", grade="Raw", condition="LP"),
    dict(category=CardCategory.pokemon, name="Umbreon VMAX", set_name="Evolving Skies", year="2021",
         card_number="215/203", variant="Alternate Art Secret", grade="Raw", condition="NM"),
    dict(category=CardCategory.mtg, name="Lightning Bolt", set_name="Revised", year="1994",
         card_number=None, variant=None, grade="Raw", condition="MP"),
    dict(category=CardCategory.mtg, name="The One Ring", set_name="Lord of the Rings: Tales of Middle-earth",
         year="2023", card_number="246", variant="Borderless", grade="Raw", condition="NM"),
    dict(category=CardCategory.yugioh, name="Blue-Eyes White Dragon", set_name="Legend of Blue Eyes",
         year="2002", card_number="LOB-001", variant="1st Edition", grade="Raw", condition="LP"),
    dict(category=CardCategory.sports, name="Jacob Misiorowski", set_name="Bowman Chrome", year="2026",
         card_number="CRA-JMI", variant="Refractor, Auto, RC", grade="Raw", is_redemption=True, is_auto=True),
]


class MockIdentifier:
    """Deterministic: the same photo always maps to the same sample card."""

    name = "mock"

    async def identify(self, image_bytes: bytes, media_type: str) -> CardIdentification:
        return sample_for(image_bytes)


def sample_for(image_bytes: bytes) -> CardIdentification:
    idx = int(hashlib.sha256(image_bytes).hexdigest(), 16) % len(_SAMPLES)
    ident = CardIdentification(**_SAMPLES[idx], confidence=0.42, search_query="")
    ident.search_query = build_search_query(ident)
    return ident
