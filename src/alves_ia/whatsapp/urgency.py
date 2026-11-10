import re
import unicodedata

_URGENT = re.compile(
    r"\b(dor.{0,24}peito|falta de ar|dificuldade.{0,12}respirar|desmai\w*|"
    r"convuls\w*|derrame|avc|suic[ií]d\w*|me matar|autoagress\w*|"
    r"sangramento.{0,20}(intenso|forte|sem parar))\b",
    re.IGNORECASE,
)


def is_urgent(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).strip()
    return bool(_URGENT.search(normalized))
