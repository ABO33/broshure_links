from __future__ import annotations

import re
from urllib.parse import urlencode


PRAKTIS_SEARCH_URL = "https://praktis.bg/catalogsearch/result"
UTM_PARAMS = {
    "utm_source": "praktis.bg",
    "utm_medium": "broshura",
    "utm_campaign": "Praktis-June-July26",
}

COLOR_WORDS = {
    "\u0431\u044f\u043b",
    "\u0431\u044f\u043b\u0430",
    "\u0431\u044f\u043b\u043e",
    "\u0431\u0435\u043b\u0438",
    "\u0447\u0435\u0440\u0435\u043d",
    "\u0447\u0435\u0440\u043d\u0430",
    "\u0447\u0435\u0440\u043d\u043e",
    "\u0447\u0435\u0440\u043d\u0438",
    "\u0441\u0438\u0432",
    "\u0441\u0438\u0432\u0430",
    "\u0441\u0438\u0432\u043e",
    "\u0441\u0438\u0432\u0438",
    "\u0437\u0435\u043b\u0435\u043d",
    "\u0437\u0435\u043b\u0435\u043d\u0430",
    "\u0437\u0435\u043b\u0435\u043d\u043e",
    "\u0437\u0435\u043b\u0435\u043d\u0438",
    "\u0441\u0438\u043d",
    "\u0441\u0438\u043d\u044f",
    "\u0441\u0438\u043d\u044c\u043e",
    "\u0441\u0438\u043d\u0438",
    "\u0447\u0435\u0440\u0432\u0435\u043d",
    "\u0447\u0435\u0440\u0432\u0435\u043d\u0430",
    "\u0447\u0435\u0440\u0432\u0435\u043d\u043e",
    "\u0447\u0435\u0440\u0432\u0435\u043d\u0438",
    "\u0436\u044a\u043b\u0442",
    "\u0436\u044a\u043b\u0442\u0430",
    "\u0436\u044a\u043b\u0442\u043e",
    "\u0436\u044a\u043b\u0442\u0438",
    "\u043a\u0430\u0444\u044f\u0432",
    "\u043a\u0430\u0444\u044f\u0432\u0430",
    "\u043a\u0430\u0444\u044f\u0432\u043e",
    "\u043a\u0430\u0444\u044f\u0432\u0438",
    "\u043f\u0440\u043e\u0437\u0440\u0430\u0447\u0435\u043d",
    "\u043f\u0440\u043e\u0437\u0440\u0430\u0447\u043d\u0430",
    "\u043f\u0440\u043e\u0437\u0440\u0430\u0447\u043d\u043e",
    "\u043c\u0430\u0442",
    "\u043c\u0430\u0442\u043e\u0432",
    "\u043c\u0430\u0442\u043e\u0432\u0430",
    "\u0433\u043b\u0430\u043d\u0446",
    "\u0433\u043b\u0430\u043d\u0446\u043e\u0432",
    "\u0433\u043b\u0430\u043d\u0446\u043e\u0432\u0430",
    "white",
    "black",
    "grey",
    "gray",
    "green",
    "blue",
    "red",
    "yellow",
    "brown",
    "transparent",
    "mat",
    "matt",
    "gloss",
}

UNIT_WORDS = {
    "\u043c\u043c",
    "mm",
    "\u0441\u043c",
    "cm",
    "\u043c",
    "m",
    "\u043b",
    "l",
    "w",
    "kw",
    "\u043a\u0432\u0442",
    "v",
    "hz",
    "\u0445\u0446",
    "kg",
    "\u043a\u0433",
    "g",
    "\u0433\u0440",
    "ml",
    "\u043c\u043b",
    "\u0431\u0440",
    "pcs",
}

COMMON_QUERY_STOP_WORDS = {
    "\u0438",
    "\u0432",
    "\u0432\u044a\u0432",
    "\u0437\u0430",
    "\u043d\u0430",
    "\u043e\u0442",
    "\u0441",
    "\u0441\u044a\u0441",
    "\u043f\u043e",
    "and",
    "for",
    "of",
    "the",
    "with",
}

NUMBER_RE = re.compile(r"^[\u00f8\u0444]?\d+(?:[.,]\d+)?(?:[x\u0445/]\d+(?:[.,]\d+)?)*$")
DIMENSION_RE = re.compile(
    r"^[\u00f8\u0444]?\d+(?:[.,]\d+)?(?:[x\u0445/]\d+(?:[.,]\d+)?)*\s*"
    r"(?:\u043c\u043c|mm|\u0441\u043c|cm|\u043c|m|\u043b|l|w|kw|\u043a\u0432\u0442|v|hz|"
    r"\u0445\u0446|kg|\u043a\u0433|g|\u0433\u0440|ml|\u043c\u043b)?$",
    re.I,
)


def make_group_search_url_from_titles(titles: list[str]) -> tuple[str, str]:
    query = common_ordered_words(titles)
    if not query:
        raise ValueError("Could not build a common search query from product titles.")
    params = {"q": query, **UTM_PARAMS}
    return f"{PRAKTIS_SEARCH_URL}?{urlencode(params)}", query


def common_ordered_words(titles: list[str]) -> str:
    token_lists = [tokenize_title(title) for title in titles if clean_title(title)]
    token_lists = remove_variant_tokens(token_lists)
    if not token_lists:
        return ""
    return common_meaningful_words(token_lists)


def common_meaningful_words(token_lists: list[list[str]]) -> str:
    normalized_sets = [{normalize_token(token) for token in tokens if normalize_token(token)} for tokens in token_lists]
    common_tokens = set.intersection(*normalized_sets) if normalized_sets else set()
    result: list[str] = []
    for token in token_lists[0]:
        norm = normalize_token(token)
        if not norm or norm not in common_tokens:
            continue
        if norm in COMMON_QUERY_STOP_WORDS or is_dimension_token(norm) or norm in COLOR_WORDS:
            continue
        result.append(token)
    return cleanup_query(" ".join(result[:8]))


def remove_variant_tokens(token_lists: list[list[str]]) -> list[list[str]]:
    normalized_sets = [{normalize_token(token) for token in tokens} for tokens in token_lists]
    common_tokens = set.intersection(*normalized_sets) if normalized_sets else set()
    cleaned_lists: list[list[str]] = []

    for tokens in token_lists:
        cleaned: list[str] = []
        skip_next_unit = False
        for token in tokens:
            norm = normalize_token(token)
            if not norm:
                continue
            if skip_next_unit and is_unit_only(norm):
                skip_next_unit = False
                continue
            skip_next_unit = False
            if norm in COLOR_WORDS:
                continue
            if is_always_variant_dimension(norm):
                if NUMBER_RE.fullmatch(norm):
                    skip_next_unit = True
                continue
            if is_dimension_token(norm) and norm not in common_tokens:
                if NUMBER_RE.fullmatch(norm):
                    skip_next_unit = True
                continue
            cleaned.append(token)
        cleaned_lists.append(cleaned)

    return cleaned_lists


def tokenize_title(title: str) -> list[str]:
    return [token.strip(" ,.;:()[]{}") for token in clean_title(title).split() if token.strip(" ,.;:()[]{}")]


def clean_title(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = re.sub(r"\s+\|\s+.*$", "", value).strip()
    value = re.sub(r"\s+-\s+Praktis.*$", "", value, flags=re.I).strip()
    return value


def cleanup_query(query: str) -> str:
    query = re.sub(r"\s+", " ", query).strip()
    query = re.sub(r"\s+/", " /", query)
    query = re.sub(r"/\s+", "/ ", query)
    return query


def normalize_token(token: str) -> str:
    return str(token or "").strip(" ,.;:()[]{}").lower()


def is_dimension_token(norm: str) -> bool:
    return bool(DIMENSION_RE.fullmatch(norm))


def is_always_variant_dimension(norm: str) -> bool:
    if any(marker in norm for marker in ("\u00f8", "\u0444", "/", "x", "\u0445")):
        return bool(DIMENSION_RE.fullmatch(norm))
    return bool(re.fullmatch(r"\d+(?:[.,]\d+)?\s*(?:w|kw|\u043a\u0432\u0442|v|hz|\u0445\u0446)", norm, re.I))


def is_unit_only(norm: str) -> bool:
    return norm in UNIT_WORDS
