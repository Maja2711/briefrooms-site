#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import publish_live_news as base
    from .news_quality import POLICY_VERSION, evaluate_story, public_policy
except ImportError:
    import publish_live_news as base
    from news_quality import POLICY_VERSION, evaluate_story, public_policy

ROOT = Path(__file__).resolve().parents[1]

_original_fetch_feed = base.fetch_feed
_original_load_previous = base.load_previous
_original_build_language = base.build_language
_original_validate = base.validate
_original_select_sections = base.select_sections
_original_round_robin = base.round_robin

# Add dependable reserve desks so no section is driven by one publisher's ordering.
PL_EDITORIAL_EXTRA_FEEDS = {
    "polityka": (
        ("Rzeczpospolita", "https://www.rp.pl/rss_main"),
    ),
}

# The canonical PL publisher used only three sports feeds. Add major Polish sports
# desks so a live national story is less dependent on one publisher's ordering.
PL_SPORT_EXTRA_FEEDS = (
    ("Interia Sport", "https://sport.interia.pl/feed"),
    ("Przegląd Sportowy / Onet Sport", "https://przegladsportowy.onet.pl/.feed"),
    ("SportoweFakty WP", "https://sportowefakty.wp.pl/rss.xml"),
)


def _extend_pl_feeds(config: Any) -> list[Any]:
    extended = []
    for section_id, label, feeds in config:
        merged = list(feeds)
        seen = {url for _, url in merged}
        additions = list(PL_EDITORIAL_EXTRA_FEEDS.get(section_id, ()))
        if section_id == "sport":
            additions.extend(PL_SPORT_EXTRA_FEEDS)
        for source, url in additions:
            if url not in seen:
                merged.append((source, url))
                seen.add(url)
        extended.append((section_id, label, merged))
    return extended


base.PL = _extend_pl_feeds(base.PL)

EDITORIAL_SELECTION_POLICY_VERSION = "public-impact-source-diversity-v1"
MAX_SOURCE_SHARE = 5

PUBLIC_IMPACT_RE = re.compile(
    r"\b(?:"
    r"rząd\w*|sejm\w*|senat\w*|prezydent\w*|minister\w*|parlament\w*|"
    r"ustaw\w*|prawo\w*|regulacj\w*|wybor\w*|referend\w*|sąd\w*|trybunał\w*|"
    r"unia\s+europejska|ue\b|nato\b|onz\b|wojn\w*|sankcj\w*|bezpieczeństw\w*|"
    r"inflacj\w*|pkb\b|stopy\s+procentowe|budżet\w*|podatek\w*|bezroboci\w*|"
    r"epidemi\w*|szczepion\w*|lek\w*|badani\w*|odkry\w*|klimat\w*|energi\w*|"
    r"cyber\w*|sztuczn\w*\s+inteligencj\w*|infrastruktur\w*|konsumenc\w*|"
    r"government|parliament|president|minister|election|referendum|law|regulat\w*|"
    r"supreme\s+court|constitutional\s+court|european\s+union|eu\b|nato\b|un\b|"
    r"war|sanctions?|security|inflation|gdp\b|interest\s+rates?|budget|tax\w*|"
    r"unemployment|epidemi\w*|vaccine\w*|medicine|research|climate|energy|"
    r"cyber\w*|artificial\s+intelligence|infrastructure|consumers?"
    r")\b",
    re.IGNORECASE,
)
GLOBAL_SCOPE_RE = re.compile(
    r"\b(?:global\w*|świat\w*|międzynarod\w*|europ\w*|usa\b|stany\s+zjednoczone|"
    r"chiny|rosj\w*|ukrain\w*|niemc\w*|francj\w*|wielka\s+brytania|"
    r"global\w*|worldwide|international|europe\w*|united\s+states|china|russia|"
    r"ukraine|germany|france|united\s+kingdom)\b",
    re.IGNORECASE,
)
MEANINGFUL_SCALE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%|proc\.?|mln|mld|tys\.?|zł|pln|eur|usd|"
    r"million|billion|percent)\b",
    re.IGNORECASE,
)
TRUSTED_DESK_RE = re.compile(
    r"\b(?:Rzeczpospolita|PAP|BBC|Guardian|Nauka w Polsce|NASA|ESA|WHO|FDA|"
    r"Bankier\.pl|Business Insider Polska|TVP Sport)\b",
    re.IGNORECASE,
)


def editorial_value_score(story: dict[str, Any], section_id: str, now: datetime) -> float:
    """Rank a story by public impact first and recency second."""
    published = _published_at(story)
    age_hours = 72.0 if published is None else max(0.0, (now - published).total_seconds() / 3600.0)
    freshness = max(-40.0, 70.0 - age_hours * 2.0)
    text = _story_text(story)
    score = freshness
    if PUBLIC_IMPACT_RE.search(text):
        score += 85.0
    if GLOBAL_SCOPE_RE.search(text):
        score += 45.0
    if MEANINGFUL_SCALE_RE.search(text):
        score += 20.0
    if TRUSTED_DESK_RE.search(str(story.get("source") or "")):
        score += 12.0
    if section_id in {"zdrowie", "nauka", "science", "health"} and re.search(
        r"\b(?:badani\w*|naukow\w*|raport\w*|research|study|scientists?|report)\b",
        text,
        re.IGNORECASE,
    ):
        score += 30.0
    return score

SPORT_LIVE_RE = re.compile(
    r"\b(?:na żywo|live|relacja live|wynik na żywo|minuta po minucie|"
    r"trwa mecz|trwa spotkanie|właśnie gra|wlasnie gra)\b|"
    r"(?:relacja-live|wynik-na-zywo|wyniki-na-zywo|liveblog)",
    re.IGNORECASE,
)
SPORT_FUTURE_RE = re.compile(
    r"\b(?:jutro|pojutrze|kiedy gra|kiedy zagra|o której|o ktorej|"
    r"gdzie oglądać|gdzie ogladac|gdzie obejrzeć|gdzie obejrzec|"
    r"zapowiedź|zapowiedz|zagra jutro|zmierzy się jutro|zmierzy sie jutro)\b",
    re.IGNORECASE,
)
SPORT_WATCHLIST_PATH = ROOT / "data" / "news" / "polish_sport_watchlist.json"
SPORT_DIVERSITY_POLICY_VERSION = "pl-sport-diversity-v1"


def _load_sport_watchlist() -> dict[str, Any]:
    payload = json.loads(SPORT_WATCHLIST_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "pl-sport-watchlist-v1":
        raise RuntimeError("Polish sport watchlist schema is missing or outdated")
    if not payload.get("athletes"):
        raise RuntimeError("Polish sport watchlist is empty")
    return payload


SPORT_WATCHLIST = _load_sport_watchlist()
TRACKED_ATHLETES = sorted(
    SPORT_WATCHLIST.get("athletes") or [],
    key=lambda item: int(item.get("rank") or 9999),
)
SPORT_SELECTION_POLICY = SPORT_WATCHLIST.get("selection_policy") or {}
MAX_STORIES_PER_TRACKED_ATHLETE = int(
    SPORT_SELECTION_POLICY.get("max_stories_per_tracked_athlete") or 2
)
MAX_STORIES_PER_REPEATED_ENTITY = int(
    SPORT_SELECTION_POLICY.get("max_stories_per_repeated_entity") or 2
)
SOFT_MAX_STORIES_PER_DISCIPLINE = int(
    SPORT_SELECTION_POLICY.get("soft_max_stories_per_discipline") or 4
)


def _alias_regex(aliases: list[str]) -> re.Pattern[str]:
    escaped = [re.escape(str(alias).strip()) for alias in aliases if str(alias).strip()]
    if not escaped:
        return re.compile(r"(?!)")
    return re.compile(r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)", re.IGNORECASE)


TRACKED_ATHLETE_PATTERNS = [
    (athlete, _alias_regex(list(athlete.get("aliases") or [athlete.get("name")])))
    for athlete in TRACKED_ATHLETES
]
_TOP_ATHLETE_ALIASES = [
    alias
    for athlete in TRACKED_ATHLETES
    if int(athlete.get("rank") or 9999) <= 6
    for alias in (athlete.get("aliases") or [athlete.get("name")])
    if alias
]
POLISH_MARQUEE_RE = _alias_regex(_TOP_ATHLETE_ALIASES)

SPORT_DISCIPLINE_PATTERNS = (
    ("tennis", re.compile(r"\b(?:tenis|WTA|ATP|US Open|Wimbledon|Roland Garros|Australian Open)\b", re.IGNORECASE)),
    ("football", re.compile(r"\b(?:piłk|pilk|Liga Mistrzów|Champions League|Barcelona|La Liga|Ekstraklasa)\b", re.IGNORECASE)),
    ("volleyball", re.compile(r"\b(?:siatk|PlusLiga|Liga Narodów|Liga Narodow)\b", re.IGNORECASE)),
    ("speedway", re.compile(r"\b(?:żuż|zuz|speedway|Grand Prix na żużlu|Grand Prix na zuzlu)\b", re.IGNORECASE)),
    ("athletics", re.compile(r"\b(?:lekkoatlet|młot|mlot|400 m|800 m|1500 m)\b", re.IGNORECASE)),
    ("sport_climbing", re.compile(r"\b(?:wspinacz|climbing)\b", re.IGNORECASE)),
    ("ski_jumping", re.compile(r"\b(?:skok(?:i|ach|ów|ow) narciarsk|Turniej Czterech Skoczni)\b", re.IGNORECASE)),
    ("cycling", re.compile(r"\b(?:kolar|Tour de France|Giro d.Italia|Vuelta)\b", re.IGNORECASE)),
)


def _matched_tracked_athletes(story: dict[str, Any]) -> list[dict[str, Any]]:
    text = _story_text(story)
    return [
        athlete
        for athlete, pattern in TRACKED_ATHLETE_PATTERNS
        if pattern.search(text)
    ]


def _sport_discipline(story: dict[str, Any]) -> str:
    tracked = _matched_tracked_athletes(story)
    if tracked:
        best = min(tracked, key=lambda item: int(item.get("rank") or 9999))
        if best.get("sport"):
            return str(best["sport"])
    text = _story_text(story)
    for discipline, pattern in SPORT_DISCIPLINE_PATTERNS:
        if pattern.search(text):
            return discipline
    return "other"


def _tracked_rank_bonus(story: dict[str, Any]) -> float:
    tracked = _matched_tracked_athletes(story)
    if not tracked:
        return 0.0
    rank = min(int(item.get("rank") or 9999) for item in tracked)
    return max(20.0, 125.0 - (rank - 1) * 4.5)


POLISH_SPORT_CONTEXT_RE = re.compile(
    r"\b(?:Polska|Polski|Polskę|Polsce|Polak|Polka|Polacy|Polki|"
    r"polski|polska|polskie|polscy|reprezentacj(?:a|i|ę)\s+Polski|"
    r"biało[- ]czerwoni|biało[- ]czerwone)\b",
    re.IGNORECASE,
)
MAJOR_SPORT_EVENT_RE = re.compile(
    r"\b(?:WTA|ATP|Grand Slam|Wimbledon|Roland Garros|US Open|Australian Open|"
    r"mistrzostw(?:a)? świata|mistrzostw(?:a)? Europy|igrzysk(?:a)?|"
    r"Liga Mistrzów|Champions League|mundial|Euro\s+20\d{2})\b",
    re.IGNORECASE,
)
SPORT_ENTITY_RE = re.compile(r"\b[A-ZĄĆĘŁŃÓŚŹŻ][A-Za-zĄąĆćĘęŁłŃńÓóŚśŹźŻż-]{3,}\b")
SPORT_ENTITY_STOP = {
    "sport", "relacja", "wynik", "mecz", "trener", "turniej", "liga", "polska",
    "polski", "polskie", "polacy", "polki", "wielkie", "mistrzostwa", "europy",
    "świata", "dzisiaj", "jutro", "tenis", "finał", "półfinał", "runda",
    "open", "puchar", "grand", "prix", "oficjalny", "komunikat", "hit", "koszmar",
}
HOME_HOT_SPORT_THRESHOLD = 430


def _story_text(story: dict[str, Any]) -> str:
    return " ".join(
        str(story.get(key) or "")
        for key in ("title", "summary", "link")
    )


def _is_live_sport(story: dict[str, Any]) -> bool:
    return bool(SPORT_LIVE_RE.search(_story_text(story)))


def _is_future_sport(story: dict[str, Any]) -> bool:
    # Use only the headline and the leading part of the RSS summary. A later mention
    # of "tomorrow" may refer to the next round after a match that is live now.
    text = f"{story.get('title', '')} {str(story.get('summary') or '')[:220]}"
    return bool(SPORT_FUTURE_RE.search(text))


def _sport_entities(story: dict[str, Any]) -> set[str]:
    title = str(story.get("title") or "")
    return {
        token.lower()
        for token in SPORT_ENTITY_RE.findall(title)
        if token.lower() not in SPORT_ENTITY_STOP
    }


def _sport_entity_support(stories: list[dict[str, Any]]) -> dict[str, int]:
    sources: dict[str, set[str]] = {}
    for story in stories:
        source = str(story.get("source") or "")
        for token in _sport_entities(story):
            sources.setdefault(token, set()).add(source)
    return {token: len(values) for token, values in sources.items()}


def _published_at(story: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(story.get("published_at") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def sport_hot_score(
    story: dict[str, Any],
    now: datetime,
    entity_support: dict[str, int] | None = None,
) -> float:
    """Rank PL sport by freshness, Polish relevance and current news heat."""
    current = now.astimezone(timezone.utc)
    published = _published_at(story)
    age_hours = 24.0 if published is None else max(0.0, (current - published).total_seconds() / 3600.0)
    freshness = max(0.0, 60.0 - age_hours * 5.0)

    text = _story_text(story)
    live = _is_live_sport(story)
    future = _is_future_sport(story)
    tracked = _matched_tracked_athletes(story)
    rank_bonus = _tracked_rank_bonus(story)
    polish = bool(tracked) or bool(POLISH_SPORT_CONTEXT_RE.search(text))
    major = bool(MAJOR_SPORT_EVENT_RE.search(text))

    support = entity_support or {}
    max_support = max((support.get(token, 1) for token in _sport_entities(story)), default=1)
    cross_source_heat = max(0, max_support - 1)

    score = freshness + rank_bonus
    if major:
        score += 35
    if polish:
        score += 35
    score += min(cross_source_heat, 4) * 80

    # A page titled "relacja live" may be published hours before an event. Future
    # language cancels the live bonus so a tomorrow preview cannot beat a match now.
    if future:
        score -= 220
    elif live:
        score += 50
        if tracked:
            score += 180 + rank_bonus * 0.35
        elif cross_source_heat >= 1 and polish:
            score += 280
        elif cross_source_heat >= 2:
            score += 180
        elif polish:
            score += 60

    return score


def _is_pl_config(config: Any) -> bool:
    return any(section_id == "polityka" for section_id, _, _ in config)


def select_sections(
    config: list[tuple[str, str, list[tuple[str, str]]]],
    fetched: dict[str, list[dict[str, Any]]],
    previous: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Select nine high-impact stories with publisher and sports diversity."""
    selected: dict[str, list[dict[str, Any]]] = {}
    health: dict[str, Any] = {}
    previous_sections = previous.get("sections") if isinstance(previous.get("sections"), dict) else {}
    global_seen: set[str] = set()
    pl_mode = _is_pl_config(config)

    for section_id, _, _ in config:
        source_candidates = list(fetched.get(section_id) or [])
        sport_mode = pl_mode and section_id == "sport"
        if sport_mode:
            support = _sport_entity_support(source_candidates)
            candidates = sorted(
                source_candidates,
                key=lambda story: (sport_hot_score(story, now, support), base.story_time(story)),
                reverse=True,
            )
        else:
            candidates = sorted(
                source_candidates,
                key=lambda story: (
                    editorial_value_score(story, section_id, now),
                    base.story_time(story),
                ),
                reverse=True,
            )

        items: list[dict[str, Any]] = []
        local_seen: set[str] = set()
        source_counts: dict[str, int] = {}
        active_sources = {
            str(story.get("source") or "").strip()
            for story in candidates
            if story.get("image") and str(story.get("source") or "").strip()
        }
        if len(active_sources) >= 3:
            preferred_source_cap = max(3, math.ceil(base.TARGET / len(active_sources)))
        elif len(active_sources) == 2:
            preferred_source_cap = MAX_SOURCE_SHARE
        else:
            preferred_source_cap = base.TARGET
        athlete_counts: dict[str, int] = {}
        entity_counts: dict[str, int] = {}
        discipline_counts: dict[str, int] = {}
        live_entities_seen: set[str] = set()
        deferred_discipline: list[dict[str, Any]] = []
        deferred_source: list[dict[str, Any]] = []

        def try_add(
            story: dict[str, Any],
            *,
            discipline_cap: bool,
            source_cap: int,
        ) -> str:
            identity = base.normalized_identity(story)
            if (
                not identity
                or identity in local_seen
                or identity in global_seen
                or not story.get("image")
            ):
                return "skip"

            source = str(story.get("source") or "").strip() or "unknown"
            if source_counts.get(source, 0) >= source_cap:
                return "source_cap"

            tracked_names: list[str] = []
            discipline = "other"
            entities: set[str] = set()
            if sport_mode:
                tracked_names = [
                    str(item.get("name") or "")
                    for item in _matched_tracked_athletes(story)
                    if item.get("name")
                ]
                if any(
                    athlete_counts.get(name, 0) >= MAX_STORIES_PER_TRACKED_ATHLETE
                    for name in tracked_names
                ):
                    return "athlete_cap"
                entities = _sport_entities(story)
                if any(
                    support.get(entity, 0) >= 2
                    and entity_counts.get(entity, 0) >= MAX_STORIES_PER_REPEATED_ENTITY
                    for entity in entities
                ):
                    return "entity_cap"
                discipline = _sport_discipline(story)
                if (
                    discipline_cap
                    and discipline != "other"
                    and discipline_counts.get(discipline, 0)
                    >= SOFT_MAX_STORIES_PER_DISCIPLINE
                ):
                    return "discipline_cap"
                if _is_live_sport(story):
                    if entities and entities & live_entities_seen:
                        return "live_duplicate"

            local_seen.add(identity)
            global_seen.add(identity)
            items.append(story)
            source_counts[source] = source_counts.get(source, 0) + 1
            if sport_mode:
                for name in tracked_names:
                    athlete_counts[name] = athlete_counts.get(name, 0) + 1
                for entity in entities:
                    entity_counts[entity] = entity_counts.get(entity, 0) + 1
                discipline_counts[discipline] = discipline_counts.get(discipline, 0) + 1
                if _is_live_sport(story):
                    live_entities_seen.update(entities)
            return "added"

        for story in candidates:
            result = try_add(
                story,
                discipline_cap=sport_mode,
                source_cap=preferred_source_cap,
            )
            if result == "discipline_cap":
                deferred_discipline.append(story)
            elif result == "source_cap":
                deferred_source.append(story)
            if len(items) >= base.TARGET:
                break

        # Discipline diversity is a soft constraint. It may be relaxed to avoid an
        # underfilled section, while the per-athlete cap remains hard.
        if sport_mode and len(items) < base.TARGET:
            for story in deferred_discipline:
                result = try_add(
                    story,
                    discipline_cap=False,
                    source_cap=preferred_source_cap,
                )
                if result == "source_cap":
                    deferred_source.append(story)
                if len(items) >= base.TARGET:
                    break

        # A publisher can exceed the preferred share only to prevent an otherwise
        # incomplete section, and never occupy more than five of nine cards when at
        # least two publishers supplied usable material.
        if len(items) < base.TARGET and preferred_source_cap < MAX_SOURCE_SHARE:
            for story in deferred_source:
                try_add(
                    story,
                    discipline_cap=False,
                    source_cap=MAX_SOURCE_SHARE,
                )
                if len(items) >= base.TARGET:
                    break

        carried = 0
        if len(items) < base.TARGET:
            old_items = previous_sections.get(section_id, []) if isinstance(previous_sections.get(section_id), list) else []
            for old in old_items:
                try:
                    published = datetime.fromisoformat(str(old.get("published_at") or "").replace("Z", "+00:00"))
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                    if now - published.astimezone(timezone.utc) > base.MAX_CARRY_AGE:
                        continue
                except Exception:
                    continue
                copy = dict(old)
                copy["carried_forward"] = True
                carry_source_cap = (
                    MAX_SOURCE_SHARE if len(active_sources) >= 2 else base.TARGET
                )
                if try_add(
                    copy,
                    discipline_cap=False,
                    source_cap=carry_source_cap,
                ) == "added":
                    carried += 1
                if len(items) >= base.TARGET:
                    break

        if len(items) < base.TARGET:
            raise RuntimeError(
                f"section {section_id} has only {len(items)} publishable stories; "
                f"target is {base.TARGET}"
            )

        selected[section_id] = items[:base.TARGET]
        times = [base.story_time(item) for item in items if base.story_time(item) > 0]
        section_health: dict[str, Any] = {
            "count": len(selected[section_id]),
            "fresh_count": len(selected[section_id]) - carried,
            "carried_count": carried,
            "newest_source_at": datetime.fromtimestamp(max(times), tz=timezone.utc).isoformat(timespec="seconds") if times else None,
            "source_mix": source_counts,
            "source_diversity_policy": EDITORIAL_SELECTION_POLICY_VERSION,
            "preferred_source_cap": preferred_source_cap,
            "hard_source_cap": MAX_SOURCE_SHARE if len(active_sources) >= 2 else base.TARGET,
        }
        if sport_mode:
            section_health["tracked_athletes"] = athlete_counts
            section_health["discipline_mix"] = discipline_counts
            section_health["diversity_policy"] = SPORT_DIVERSITY_POLICY_VERSION
        health[section_id] = section_health

    return selected, health


def round_robin(
    sections: dict[str, list[dict[str, Any]]],
    labels: dict[str, str],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Put a genuinely hot PL live-sport story first on the homepage."""
    output = _original_round_robin(sections, labels, limit)
    if "polityka" not in sections or "sport" not in sections or not sections.get("sport"):
        return output

    now = datetime.now(timezone.utc)
    support = _sport_entity_support(sections["sport"])
    hot_story = max(
        sections["sport"],
        key=lambda story: sport_hot_score(story, now, support),
    )
    hot_score = sport_hot_score(hot_story, now, support)

    if (
        hot_score < HOME_HOT_SPORT_THRESHOLD
        or not _is_live_sport(hot_story)
        or _is_future_sport(hot_story)
    ):
        return output

    promoted = dict(hot_story)
    promoted["category"] = labels.get("sport", "Sport")
    identity = base.normalized_identity(promoted)
    rest = [story for story in output if base.normalized_identity(story) != identity]
    return [promoted, *rest][:limit]


def _filter_stories(stories: list[dict[str, Any]], context: str) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for story in stories:
        decision = evaluate_story(story.get("title"), story.get("summary"))
        if decision.accepted:
            accepted.append(story)
            continue
        print(f"EDITORIAL_FILTER {context} reason={decision.reason} title={story.get('title')!r}")
    return accepted


def _refresh_en_article_images(payload: dict[str, Any]) -> tuple[int, int]:
    """Prefer each EN article's canonical OG/Twitter image over its RSS thumbnail.

    This runs only after the EN selection is complete, so the existing PL image path is
    left entirely untouched and we fetch at most the stories that are actually published.
    """
    sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    stories_by_identity: dict[str, dict[str, Any]] = {}

    for stories in sections.values():
        if not isinstance(stories, list):
            continue
        for story in stories:
            if not isinstance(story, dict):
                continue
            identity = base.normalized_identity(story)
            link = base.safe_url(story.get("link"))
            if identity and link:
                stories_by_identity[identity] = story

    refreshed = 0
    if stories_by_identity:
        jobs: dict[Any, str] = {}
        with ThreadPoolExecutor(max_workers=min(base.MAX_WORKERS, len(stories_by_identity))) as pool:
            for identity, story in stories_by_identity.items():
                jobs[pool.submit(base.page_image, story["link"])] = identity
            for future in as_completed(jobs):
                image = future.result()
                if not image:
                    continue
                stories_by_identity[jobs[future]]["image"] = image
                refreshed += 1

    published_images = {
        identity: story.get("image")
        for identity, story in stories_by_identity.items()
        if story.get("image")
    }
    home = payload.get("home") if isinstance(payload.get("home"), list) else []
    for story in home:
        if not isinstance(story, dict):
            continue
        image = published_images.get(base.normalized_identity(story))
        if image:
            story["image"] = image

    return refreshed, len(stories_by_identity)


def fetch_feed(source: str, feed_url: str, section_id: str, now: Any) -> tuple[list[dict[str, Any]], str | None]:
    stories, error = _original_fetch_feed(source, feed_url, section_id, now)
    accepted = _filter_stories(stories, f"fresh/{section_id}/{source}")
    if section_id == "sport":
        # The base image pass is intentionally bounded. Make sure a live/high-profile
        # candidate cannot disappear only because its RSS item omitted a thumbnail.
        for story in accepted:
            if story.get("image"):
                continue
            text = _story_text(story)
            if _is_live_sport(story) or _matched_tracked_athletes(story):
                story["image"] = base.page_image(str(story.get("link") or ""))
    return accepted, error


def load_previous(lang: str) -> dict[str, Any]:
    payload = _original_load_previous(lang)
    sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    for section_id, stories in list(sections.items()):
        if isinstance(stories, list):
            sections[section_id] = _filter_stories(stories, f"carried/{lang}/{section_id}")
    payload["sections"] = sections
    return payload


def build_language(lang: str, config: Any, marker: str, now: Any) -> dict[str, Any]:
    payload = _original_build_language(lang, config, marker, now)
    payload["editorial_policy"] = public_policy()
    payload.setdefault("health", {})["editorial_filter"] = {
        "status": "active",
        "version": POLICY_VERSION,
    }
    payload["health"]["editorial_selection"] = {
        "status": "active",
        "mode": "public_impact_then_recency_with_publisher_diversity",
        "version": EDITORIAL_SELECTION_POLICY_VERSION,
        "max_cards_per_source_when_multiple_sources_available": MAX_SOURCE_SHARE,
    }
    if lang == "pl":
        payload["health"]["sport_hot_priority"] = {
            "status": "active",
            "mode": "ranked_polish_athletes_plus_cross_source_heat_and_diversity",
            "homepage_promotion_threshold": HOME_HOT_SPORT_THRESHOLD,
            "extra_sources": [source for source, _ in PL_SPORT_EXTRA_FEEDS],
            "watchlist_version": SPORT_WATCHLIST.get("schema_version"),
            "watchlist_path": "/data/news/polish_sport_watchlist.json",
            "tracked_athletes": len(TRACKED_ATHLETES),
            "max_stories_per_tracked_athlete": MAX_STORIES_PER_TRACKED_ATHLETE,
            "max_stories_per_repeated_entity": MAX_STORIES_PER_REPEATED_ENTITY,
            "soft_max_stories_per_discipline": SOFT_MAX_STORIES_PER_DISCIPLINE,
            "section_target": base.TARGET,
        }
    if lang == "en":
        refreshed, selected = _refresh_en_article_images(payload)
        payload["health"]["image_quality"] = {
            "status": "active",
            "scope": "en_only",
            "mode": "article_og_image_preferred",
            "refreshed_count": refreshed,
            "selected_count": selected,
        }
    return payload


def validate(max_age_minutes: int = 30) -> None:
    _original_validate(max_age_minutes)
    for lang in ("pl", "en"):
        path = ROOT / "data" / "news" / f"{lang}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        policy = payload.get("editorial_policy") or {}
        if policy.get("version") != POLICY_VERSION:
            raise RuntimeError(f"{lang} editorial policy missing or outdated")
        editorial_selection = (payload.get("health") or {}).get("editorial_selection") or {}
        if editorial_selection.get("version") != EDITORIAL_SELECTION_POLICY_VERSION:
            raise RuntimeError(f"{lang} editorial selection policy missing or outdated")
        if lang == "pl":
            hot_priority = (payload.get("health") or {}).get("sport_hot_priority") or {}
            if hot_priority.get("mode") != "ranked_polish_athletes_plus_cross_source_heat_and_diversity":
                raise RuntimeError("pl sports diversity policy missing or outdated")
            if hot_priority.get("watchlist_version") != "pl-sport-watchlist-v1":
                raise RuntimeError("pl sports watchlist missing or outdated")
            if int(hot_priority.get("section_target") or 0) != base.TARGET:
                raise RuntimeError("pl sports section target is inconsistent")
        if lang == "en":
            image_quality = (payload.get("health") or {}).get("image_quality") or {}
            if image_quality.get("scope") != "en_only" or image_quality.get("mode") != "article_og_image_preferred":
                raise RuntimeError("en high-resolution image policy missing or outdated")
        for section_id, stories in payload.get("sections", {}).items():
            if len(stories) != base.TARGET:
                raise RuntimeError(
                    f"{lang}/{section_id} has {len(stories)} stories; expected {base.TARGET}"
                )
            if lang == "pl" and section_id == "sport":
                athlete_counts: dict[str, int] = {}
                for sport_story in stories:
                    for athlete in _matched_tracked_athletes(sport_story):
                        name = str(athlete.get("name") or "")
                        athlete_counts[name] = athlete_counts.get(name, 0) + 1
                offenders = {
                    name: count
                    for name, count in athlete_counts.items()
                    if count > MAX_STORIES_PER_TRACKED_ATHLETE
                }
                if offenders:
                    raise RuntimeError(f"pl/sport violates athlete diversity cap: {offenders}")
            source_counts: dict[str, int] = {}
            for story in stories:
                source = str(story.get("source") or "").strip() or "unknown"
                source_counts[source] = source_counts.get(source, 0) + 1
                decision = evaluate_story(story.get("title"), story.get("summary"))
                if not decision.accepted:
                    raise RuntimeError(
                        f"{lang}/{section_id} contains blocked story ({decision.reason}): {story.get('title')}"
                    )
            if len(source_counts) >= 2 and max(source_counts.values()) > MAX_SOURCE_SHARE:
                raise RuntimeError(
                    f"{lang}/{section_id} violates publisher share cap: {source_counts}"
                )


base.fetch_feed = fetch_feed
base.load_previous = load_previous
base.select_sections = select_sections
base.round_robin = round_robin
base.build_language = build_language
base.validate = validate


if __name__ == "__main__":
    base.main()
