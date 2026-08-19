#!/usr/bin/env python3
from __future__ import annotations

import json
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

# The canonical PL publisher used only three sports feeds. Add major Polish sports
# desks so a live national story is less dependent on one publisher's ordering.
PL_SPORT_EXTRA_FEEDS = (
    ("Interia Sport", "https://sport.interia.pl/feed"),
    ("Przegląd Sportowy / Onet Sport", "https://przegladsportowy.onet.pl/.feed"),
    ("SportoweFakty WP", "https://sportowefakty.wp.pl/rss.xml"),
    ("Eurosport Polska", "https://eurosport.tvn24.pl/rss.xml"),
)


def _extend_pl_sport_feeds(config: Any) -> list[Any]:
    extended = []
    for section_id, label, feeds in config:
        if section_id != "sport":
            extended.append((section_id, label, list(feeds)))
            continue
        merged = list(feeds)
        seen = {url for _, url in merged}
        for source, url in PL_SPORT_EXTRA_FEEDS:
            if url not in seen:
                merged.append((source, url))
                seen.add(url)
        extended.append((section_id, label, merged))
    return extended


base.PL = _extend_pl_sport_feeds(base.PL)

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
POLISH_MARQUEE_RE = re.compile(
    r"\b(?:Iga\s+Świątek|Świątek|Robert\s+Lewandowski|Lewandowski|"
    r"reprezentacj(?:a|i|ę)\s+Polski|biało[- ]czerwoni|biało[- ]czerwone)\b",
    re.IGNORECASE,
)
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
    """Editorial urgency score for PL sport.

    Current live events involving a top Polish name win over generic recency.
    Repeated coverage across independent sports desks acts as a dynamic "what is hot"
    signal, so the system is not limited to a fixed list of athletes.
    """
    current = now.astimezone(timezone.utc)
    published = _published_at(story)
    age_hours = 24.0 if published is None else max(0.0, (current - published).total_seconds() / 3600.0)
    freshness = max(0.0, 60.0 - age_hours * 5.0)

    text = _story_text(story)
    live = _is_live_sport(story)
    future = _is_future_sport(story)
    marquee = bool(POLISH_MARQUEE_RE.search(text))
    polish = marquee or bool(POLISH_SPORT_CONTEXT_RE.search(text))
    major = bool(MAJOR_SPORT_EVENT_RE.search(text))

    support = entity_support or {}
    max_support = max((support.get(token, 1) for token in _sport_entities(story)), default=1)
    cross_source_heat = max(0, max_support - 1)

    score = freshness
    if major:
        score += 35
    if polish:
        score += 35
    if marquee:
        score += 120
    score += min(cross_source_heat, 4) * 80

    # A page titled "relacja live" may be published hours before an event. Future
    # language cancels the live bonus so a tomorrow preview cannot beat a match now.
    if future:
        score -= 220
    elif live:
        score += 50
        if marquee:
            score += 420
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
    """Preserve canonical selection, but rank the PL Sport section by live relevance."""
    selected: dict[str, list[dict[str, Any]]] = {}
    health: dict[str, Any] = {}
    previous_sections = previous.get("sections") if isinstance(previous.get("sections"), dict) else {}
    global_seen: set[str] = set()
    pl_mode = _is_pl_config(config)

    for section_id, _, _ in config:
        source_candidates = list(fetched.get(section_id) or [])
        if pl_mode and section_id == "sport":
            support = _sport_entity_support(source_candidates)
            candidates = sorted(
                source_candidates,
                key=lambda story: (sport_hot_score(story, now, support), base.story_time(story)),
                reverse=True,
            )
        else:
            candidates = sorted(source_candidates, key=base.story_time, reverse=True)

        items: list[dict[str, Any]] = []
        local_seen: set[str] = set()
        live_entities_seen: set[str] = set()

        for story in candidates:
            identity = base.normalized_identity(story)
            if not identity or identity in local_seen or identity in global_seen or not story.get("image"):
                continue

            if pl_mode and section_id == "sport" and _is_live_sport(story):
                entities = _sport_entities(story)
                # Avoid filling the six cards with several publishers' live pages for
                # exactly the same athlete/event; keep the highest-ranked one.
                if entities and entities & live_entities_seen:
                    continue
                live_entities_seen.update(entities)

            local_seen.add(identity)
            global_seen.add(identity)
            items.append(story)
            if len(items) >= base.TARGET:
                break

        carried = 0
        if len(items) < base.TARGET:
            old_items = previous_sections.get(section_id, []) if isinstance(previous_sections.get(section_id), list) else []
            for old in old_items:
                identity = base.normalized_identity(old)
                if not identity or identity in local_seen or identity in global_seen or not old.get("image"):
                    continue
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
                local_seen.add(identity)
                global_seen.add(identity)
                items.append(copy)
                carried += 1
                if len(items) >= base.TARGET:
                    break

        if len(items) < base.MIN_SECTION:
            raise RuntimeError(f"section {section_id} has only {len(items)} publishable stories; minimum is {base.MIN_SECTION}")

        selected[section_id] = items[:base.TARGET]
        times = [base.story_time(item) for item in items if base.story_time(item) > 0]
        health[section_id] = {
            "count": len(selected[section_id]),
            "fresh_count": len(selected[section_id]) - carried,
            "carried_count": carried,
            "newest_source_at": datetime.fromtimestamp(max(times), tz=timezone.utc).isoformat(timespec="seconds") if times else None,
        }

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
            if _is_live_sport(story) or POLISH_MARQUEE_RE.search(text):
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
    if lang == "pl":
        payload["health"]["sport_hot_priority"] = {
            "status": "active",
            "mode": "live_polish_star_plus_cross_source_heat",
            "homepage_promotion_threshold": HOME_HOT_SPORT_THRESHOLD,
            "extra_sources": [source for source, _ in PL_SPORT_EXTRA_FEEDS],
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
        if lang == "pl":
            hot_priority = (payload.get("health") or {}).get("sport_hot_priority") or {}
            if hot_priority.get("mode") != "live_polish_star_plus_cross_source_heat":
                raise RuntimeError("pl sports hot-priority policy missing or outdated")
        if lang == "en":
            image_quality = (payload.get("health") or {}).get("image_quality") or {}
            if image_quality.get("scope") != "en_only" or image_quality.get("mode") != "article_og_image_preferred":
                raise RuntimeError("en high-resolution image policy missing or outdated")
        for section_id, stories in payload.get("sections", {}).items():
            for story in stories:
                decision = evaluate_story(story.get("title"), story.get("summary"))
                if not decision.accepted:
                    raise RuntimeError(
                        f"{lang}/{section_id} contains blocked story ({decision.reason}): {story.get('title')}"
                    )


base.fetch_feed = fetch_feed
base.load_previous = load_previous
base.select_sections = select_sections
base.round_robin = round_robin
base.build_language = build_language
base.validate = validate


if __name__ == "__main__":
    base.main()
