import base64
import json
import re

from anthropic import AsyncAnthropic

from .base import CardIdentification, build_search_query

_PROMPT = """You are identifying a single collectible trading card from a photo.
It may be a sports card, Pokémon, Magic: The Gathering, Yu-Gi-Oh!, another TCG, or something else.
If it is in a grading slab, read the grade from the label (e.g. "PSA 10", "BGS 9.5", "CGC 9").

Respond with ONLY a JSON object, no prose and no code fences, with these keys:
{
  "category": "sports" | "pokemon" | "mtg" | "yugioh" | "other_tcg" | "other",
  "name": string,             // player name for sports, card name for TCG
  "set_name": string | null,  // e.g. "Topps Chrome", "Base Set", "Revised"
  "year": string | null,
  "card_number": string | null,  // exactly as printed, letter-only codes too, e.g. "4/102", "LOB-001", "136", "CRA-JMI"
  "variant": string | null,   // parallel, foil, refractor, 1st Edition, alt art, rookie...
  "grade": string | null,     // slab grade, or "Raw" if ungraded
  "condition": string | null, // for raw cards only: NM, LP, MP, HP, DMG (best visual estimate)
  "confidence": number,       // 0-1, your confidence in name+set+number
  "search_query": string,     // concise eBay search query that would find this exact card
  "is_redemption": true | false, // a redemption/exchange card, e.g. "You are due to receive..."
  "is_auto": true | false     // the card is autographed or is an autograph card
}
Use null for anything you cannot read. Do not guess card numbers you cannot see."""


class ClaudeIdentifier:
    name = "claude"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("IDENTIFIER=claude requires ANTHROPIC_API_KEY")
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def identify(self, image_bytes: bytes, media_type: str) -> CardIdentification:
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type,
                        "data": base64.b64encode(image_bytes).decode(),
                    }},
                    {"type": "text", "text": _PROMPT},
                ],
            }],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        ident = CardIdentification.model_validate(json.loads(text))
        if not ident.search_query:
            ident.search_query = build_search_query(ident)
        return ident
