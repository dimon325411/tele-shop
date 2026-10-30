"""Check the AI settings from .env with one test request (run on the computer where the bot runs).

    python scripts/ai_check.py

Prints which provider/model/URL is used, the raw answer of the model to a sample deal and how the
bot understood it. On errors it says what usually causes them (server not started, wrong model name...).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.domain.models import StoredListing  # noqa: E402
from app.services.ai import OLLAMA_URL, SCREEN_PROMPT, build_provider, describe_listing, parse_screen  # noqa: E402

HINTS = """Najczęstsze przyczyny / частые причины:
* Ollama: program nie działa -> uruchom Ollama (ikona w zasobniku) albo `ollama serve`;
  AI_MODEL musi być DOKŁADNIE taki jak w `ollama list` (np. qwen3:8b).
* LM Studio: zakładka Developer -> Start Server (port 1234), model musi być załadowany (Load);
  AI_PROVIDER=openai_compat, AI_BASE_URL=http://localhost:1234/v1, AI_MODEL = identyfikator modelu z LM Studio.
* Modele ":cloud" w Ollama nie są lokalne (wysyłają dane do chmury i mają limity)."""


async def main() -> int:
    s = get_settings()
    url = s.ai_base_url or (OLLAMA_URL if s.ai_provider == "ollama" else "")
    print(f"AI_PROVIDER={s.ai_provider}  AI_MODEL={s.ai_model or '(puste)'}  URL={url or '-'}  VISION={s.ai_vision}")
    provider = build_provider(s)
    if provider is None:
        print("AI nie jest skonfigurowane: ustaw AI_PROVIDER i AI_MODEL w .env (docs/AI.md).")
        return 1
    now = datetime.now(timezone.utc)
    listing = StoredListing(source="olx", external_id="test", url="https://www.olx.pl/", title="iPhone 15 Pro 128GB",
                            price=Decimal(1700), currency="PLN", condition="used",
                            description="Telefon na części, blokada iCloud, nie włącza się.", posted_at=now)
    text = "DANE (JSON):\n" + json.dumps({"ogłoszenie": describe_listing(listing, now), "mediana_podobnych": "2050 PLN",
                                         "procent_poniżej_mediany": 17}, ensure_ascii=False, default=str)
    started = datetime.now()
    try:
        reply = await provider.complete(SCREEN_PROMPT, text, None)
    except Exception as exc:
        print(f"BŁĄD / ОШИБКА: {exc!r}")
        print(HINTS)
        return 2
    seconds = (datetime.now() - started).total_seconds()
    print(f"Odpowiedź modelu ({seconds:.1f} s):\n{reply.text}\n")
    verdict = parse_screen(reply.text)
    if verdict is None:
        print("Bot NIE zrozumiał odpowiedzi (brak JSON). Wybierz inny model albo zgłoś to.")
        return 3
    print(f"Bot zrozumiał: decyzja={verdict.decision}, powód={verdict.reason}")
    print("OK" if verdict.decision == "reject" else "Uwaga: model nie odrzucił oczywistej blokady iCloud.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
