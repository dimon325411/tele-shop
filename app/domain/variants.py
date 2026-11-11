"""Product variant detection for resellers.

Prices are comparable only within the same configuration: an iPhone 15 Pro 128 GB and 512 GB are
different markets. ``variant_of`` extracts the storage size from the title; ``is_junk`` flags listings
that are not a working device (damaged, parts, locked, accessories) so they never pollute the median
or show up as a "deal".
"""

from __future__ import annotations

import re

from app.domain.text import normalize

_STORAGE = re.compile(r"\b(32|64|128|256|512)\s*gb\b|\b(1|2)\s*tb\b")

# anywhere in the title: damaged / locked / not a sale of a device
JUNK_WORDS = [
    "uszkodzony", "uszkodzona", "uszkodzone", "pekniety", "peknieta", "pekniete", "zbity", "zbita",
    "nie dziala", "niedzialajacy", "na czesci", "do naprawy", "zalany", "zalana", "bootloop", "wadliwy",
    "icloud", "blokada", "zablokowany", "zablokowana", "simlock",
    "zamienie", "zamiana", "kupie", "skupuje", "skup", "atrapa", "makieta",
    "zarezerwowane", "zarezerwowany", "zarezerwowana", "rezerwacja", "sprzedane", "sprzedany", "sprzedana",
]
# several different models in one title ("iPhone 15 Pro Max iPhone 16 Pro ...") = a shop list, not one device
_MODEL_NUMBER = re.compile(r"\b(?:iphone|rtx|gtx|rx)\s*(\d{2,4})")
# only when the title STARTS with them ("Etui do iPhone 15 Pro"), since good offers often
# list extras ("iPhone 15 Pro + etui + pudelko")
ACCESSORY_PREFIXES = ["etui", "case", "szklo", "szybka", "folia", "obudowa", "ladowarka", "kabel", "pudelko",
                      "plecki", "wyswietlacz", "bateria", "pasek", "uchwyt", "korpus", "klapka", "czesci"]
_JUNK = [w.split() for w in JUNK_WORDS]


def variant_of(title: str | None, category: str | None = None) -> str | None:
    """A configuration key, with RAM, storage and VRAM kept in separate dimensions."""
    text = normalize(title)
    laptop = category == 'laptopy' or 'macbook' in text or 'laptop' in text
    desktop = category == 'komputery' or bool(re.search(r'\b(?:komputer|pc|zestaw)\b', text))
    gpu = category == 'karty_graficzne' or bool(re.search(r'\b(?:rtx|gtx|rx)\s*\d', text))
    if laptop or desktop:
        ram = re.search(r'\b(4|8|16|24|32|48|64|96|128)\s*gb\s*(?:ram|ddr\d?)\b', text)
        if ram is None:
            ram = re.search(r'\b(?:ram|ddr\d?)\s*(4|8|16|24|32|48|64|96|128)\s*gb\b', text)
        compact = re.search(r'\b(4|8|16|24|32|48|64|96|128)\s*/\s*(128|256|512|1024|2048)\b', title or '') if laptop else None
        disks = [m for m in _STORAGE.finditer(text) if ram is None or not (ram.start() <= m.start() < ram.end())]
        disk = disks[0] if disks else None
        if compact:
            ram_size = compact.group(1)
            size = int(compact.group(2))
            storage = f'{size // 1024}tb' if size >= 1024 else f'{size}gb'
        elif ram is not None and disk is not None:
            ram_size = ram.group(1)
            storage = f'{disk.group(1)}gb' if disk.group(1) else f'{disk.group(2)}tb'
        else:
            return None
        cpu = re.search(r'\b(i[3579])\s*(\d{4,5}[a-z]*)\b|\bryzen\s*([3579])\s*(\d{4}[a-z]*)\b', text)
        if desktop and cpu is None:
            return None
        model = product_model(title)
        if desktop:
            graphics = re.search(r'\b(?:rtx|gtx|rx)\s*\d{3,4}(?:\s*(?:ti super|ti|super|xtx|xt))?\b', text)
            if graphics is None:
                return None
            memory = re.search(r'\b(4|6|8|10|11|12|16|20|24|32|48)\s*g(?:b)?\b', text[graphics.end():])
            if memory is None or (ram and ram.start() <= graphics.end() + memory.start() < ram.end()):
                return None
            gpu_key = graphics.group().replace(' ', '') + 'vram' + memory.group(1) + 'gb'
            cpu_key = (cpu.group(1) + cpu.group(2)) if cpu.group(1) else ('ryzen' + cpu.group(3) + cpu.group(4))
            prefix = cpu_key + '_' + gpu_key + '_'
        else:
            if model is None:
                return None
            prefix = model + '_'
        # Do not label an unspecified disk (or an HDD) as SSD.
        disk_type = 'ssd' if laptop or 'ssd' in text else ('hdd' if 'hdd' in text else 'disk')
        return f'{prefix}ram{ram_size}gb_{disk_type}{storage}'
    if gpu:
        m = re.search(r'\b(4|6|8|10|11|12|16|20|24|32|48)\s*g(?:b)?\b', text)
        model = product_model(title)
        return f'{model}_vram{m.group(1)}gb' if m and model else None
    m = _STORAGE.search(text)
    if not m:
        return None
    return f"{m.group(1)}gb" if m.group(1) else f"{m.group(2)}tb"


def product_model(title: str | None) -> str | None:
    text = normalize(title)
    patterns = [r'\biphone\s+(?:air|\d{1,2}[a-z]?(?:\s+(?:pro max|pro|plus))?)\b',
                r'\bmacbook\s+(?:air|pro)\s+m\d(?:\s+(?:pro|max|ultra))?\b',
                r'\b(?:rtx|gtx|rx)\s*\d{3,4}(?:\s*(?:ti super|ti|super|xtx|xt))?\b']
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group().replace(' ', '')
    return None


def listing_variant(listing) -> str | None:
    return variant_of(listing.title, listing.category)


def needs_configuration(listing) -> bool:
    text = normalize(listing.title)
    return listing.category in {'iphone', 'laptopy', 'komputery', 'karty_graficzne'} or bool(
        re.search(r'\b(?:iphone|macbook|laptop|komputer|pc|rtx|gtx|rx)\b', text))


def has_description_defect(description: str | None) -> bool:
    """Conservative affirmative defect signals; negated claims are not defects."""
    text = normalize(description)
    text = re.sub(r'\b(?:bez|brak)\s+(?:blokady?|icloud|uszkodzen|wad)\b(?:\s+icloud)?', '', text)
    text = re.sub(r'\b(?:nie jest|nie byl)\s+(?:uszkodzony|zalany)\b', '', text)
    return bool(re.search(r'\b(?:uszkodzon[aye]|uszkodzony|nie dziala|na czesci|do naprawy|'
                          r'blokada icloud|zablokowany|zalany|zbity ekran)\b', text))


def is_junk(title: str | None) -> bool:
    """True for damaged/locked devices, parts, accessories, swap or "will buy" posts (title only)."""
    tokens = normalize(title).split() if title else []
    if not tokens:
        return False
    if tokens[0] in ACCESSORY_PREFIXES or (tokens[0] in {"nowe", "nowy", "nowa", "oryginalne", "oryginalny"}
                                           and len(tokens) > 1 and tokens[1] in ACCESSORY_PREFIXES):
        return True
    for words in _JUNK:
        n = len(words)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == words:
                return True
    return len(set(_MODEL_NUMBER.findall(" ".join(tokens)))) > 1
