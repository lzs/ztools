#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch an Outlook iCalendar URL, extract events for the next "
            "three days, and print Markdown."
        )
    )
    parser.add_argument(
        "ics_url",
        nargs="?",
        help=(
            "Microsoft Outlook ICS URL. If omitted, the script reads "
            "OUTLOOK_ICS_URL from .env in the current directory."
        ),
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help=(
            "IANA timezone used for filtering and output. Defaults to the "
            "system local timezone."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=3,
        help="Number of days ahead to include, starting from now. Default: 3.",
    )
    return parser.parse_args()


def read_dotenv(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if value and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                env[key] = value
    except FileNotFoundError:
        return {}
    return env


def resolve_ics_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value

    dotenv_values = read_dotenv(".env")
    env_value = dotenv_values.get("OUTLOOK_ICS_URL") or os.environ.get("OUTLOOK_ICS_URL")
    if env_value:
        return env_value

    raise ValueError(
        "No ICS URL provided. Pass ics_url explicitly or set OUTLOOK_ICS_URL in .env."
    )


@dataclass
class Event:
    start: datetime
    end: datetime
    summary: str
    location: str
    description: str
    all_day: bool
    source_tzid: str | None = None


@dataclass
class PropertyValue:
    value: str
    params: dict[str, str]


def normalize_url(url: str) -> str:
    if url.startswith("webcal://"):
        return "https://" + url[len("webcal://") :]
    return url


def fetch_ics(url: str) -> str:
    request = urllib.request.Request(
        normalize_url(url),
        headers={
            "User-Agent": "ztools-outlook-ics-to-markdown/1.0",
            "Accept": "text/calendar,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def unfold_ics_lines(raw_text: str) -> list[str]:
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in lines:
        if not unfolded:
            unfolded.append(line)
            continue
        if line.startswith((" ", "\t")):
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def parse_property(line: str) -> tuple[str, PropertyValue]:
    if ":" not in line:
        return line.upper(), PropertyValue("", {})

    left, value = line.split(":", 1)
    parts = left.split(";")
    name = parts[0].upper()
    params: dict[str, str] = {}
    for chunk in parts[1:]:
        if "=" in chunk:
            key, raw = chunk.split("=", 1)
            params[key.upper()] = raw.strip('"')
    return name, PropertyValue(unescape_ics_text(value), params)


def unescape_ics_text(value: str) -> str:
    return (
        value.replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_datetime(value: str, params: dict[str, str], default_tz: ZoneInfo) -> tuple[datetime, bool, str | None]:
    value_type = params.get("VALUE", "").upper()
    tzid = params.get("TZID")

    if value_type == "DATE":
        parsed_date = datetime.strptime(value, "%Y%m%d").date()
        return datetime.combine(parsed_date, time.min, tzinfo=default_tz), True, tzid

    if value.endswith("Z"):
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return parsed.astimezone(default_tz), False, tzid

    parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
    event_tz = default_tz
    if tzid:
        try:
            event_tz = ZoneInfo(tzid)
        except Exception:
            event_tz = default_tz
    return parsed.replace(tzinfo=event_tz).astimezone(default_tz), False, tzid


def parse_duration(duration_text: str) -> timedelta:
    sign = -1 if duration_text.startswith("-") else 1
    if duration_text[:1] in {"+", "-"}:
        duration_text = duration_text[1:]

    if not duration_text.startswith("P"):
        raise ValueError(f"Unsupported duration format: {duration_text}")

    days = hours = minutes = seconds = 0
    in_time = False
    number = ""
    for char in duration_text[1:]:
        if char == "T":
            in_time = True
            continue
        if char.isdigit():
            number += char
            continue
        amount = int(number or "0")
        number = ""
        if char == "D":
            days = amount
        elif char == "H":
            hours = amount
        elif char == "M":
            if in_time:
                minutes = amount
            else:
                days += amount * 30
        elif char == "S":
            seconds = amount

    return sign * timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_rrule(rule_text: str) -> dict[str, str]:
    rule: dict[str, str] = {}
    for part in rule_text.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            rule[key.upper()] = value
    return rule


def weekday_to_ical(day: date) -> str:
    return ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][day.weekday()]


def build_event(properties: dict[str, list[PropertyValue]], default_tz: ZoneInfo) -> Event | None:
    start_prop = first_property(properties, "DTSTART")
    if not start_prop:
        return None

    start, all_day, source_tzid = parse_datetime(start_prop.value, start_prop.params, default_tz)

    end_prop = first_property(properties, "DTEND")
    duration_prop = first_property(properties, "DURATION")
    if end_prop:
        end, _, _ = parse_datetime(end_prop.value, end_prop.params, default_tz)
    elif duration_prop:
        end = start + parse_duration(duration_prop.value)
    elif all_day:
        end = start + timedelta(days=1)
    else:
        end = start

    return Event(
        start=start,
        end=end,
        summary=property_text(properties, "SUMMARY", "Untitled event"),
        location=property_text(properties, "LOCATION", ""),
        description=property_text(properties, "DESCRIPTION", ""),
        all_day=all_day,
        source_tzid=source_tzid,
    )


def first_property(properties: dict[str, list[PropertyValue]], name: str) -> PropertyValue | None:
    values = properties.get(name)
    return values[0] if values else None


def property_text(properties: dict[str, list[PropertyValue]], name: str, default: str) -> str:
    prop = first_property(properties, name)
    return prop.value.strip() if prop and prop.value.strip() else default


def parse_events(raw_text: str, default_tz: ZoneInfo, window_end: datetime) -> list[Event]:
    lines = unfold_ics_lines(raw_text)
    events: list[Event] = []
    in_event = False
    properties: dict[str, list[PropertyValue]] = {}

    for line in lines:
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            in_event = True
            properties = {}
            continue
        if stripped == "END:VEVENT":
            base_event = build_event(properties, default_tz)
            if base_event:
                events.extend(expand_event(properties, base_event, default_tz, window_end))
            in_event = False
            properties = {}
            continue
        if not in_event or not stripped:
            continue

        name, value = parse_property(stripped)
        properties.setdefault(name, []).append(value)

    events.sort(key=lambda item: (item.start, item.end, item.summary.lower()))
    return events


def expand_event(
    properties: dict[str, list[PropertyValue]],
    base_event: Event,
    default_tz: ZoneInfo,
    window_end: datetime,
) -> list[Event]:
    if property_text(properties, "STATUS", "").upper() == "CANCELLED":
        return []

    if first_property(properties, "RECURRENCE-ID"):
        return [base_event]

    rrule_prop = first_property(properties, "RRULE")
    if not rrule_prop:
        return [base_event]

    rule = parse_rrule(rrule_prop.value)
    freq = rule.get("FREQ", "").upper()
    if freq not in {"DAILY", "WEEKLY"}:
        return [base_event]

    interval = int(rule.get("INTERVAL", "1") or "1")
    count = int(rule["COUNT"]) if "COUNT" in rule else None
    until = None
    if "UNTIL" in rule:
        until, _, _ = parse_datetime(rule["UNTIL"], {}, default_tz)

    exdates = collect_exdates(properties, default_tz)
    byday = set(rule.get("BYDAY", "").split(",")) if rule.get("BYDAY") else set()
    if freq == "WEEKLY" and not byday:
        byday = {weekday_to_ical(base_event.start.date())}

    duration = base_event.end - base_event.start
    occurrences: list[Event] = []
    current = base_event.start
    emitted = 0

    while current <= window_end:
        if until and current > until:
            break

        include = False
        if freq == "DAILY":
            delta_days = (current.date() - base_event.start.date()).days
            include = delta_days % interval == 0
        elif freq == "WEEKLY":
            delta_days = (current.date() - base_event.start.date()).days
            if delta_days >= 0:
                weeks = delta_days // 7
                include = weeks % interval == 0
                if byday:
                    include = include and weekday_to_ical(current.date()) in byday

        if include and current not in exdates:
            occurrences.append(
                Event(
                    start=current,
                    end=current + duration,
                    summary=base_event.summary,
                    location=base_event.location,
                    description=base_event.description,
                    all_day=base_event.all_day,
                    source_tzid=base_event.source_tzid,
                )
            )
            emitted += 1
            if count is not None and emitted >= count:
                break

        current += timedelta(days=1)

    return occurrences or [base_event]


def collect_exdates(properties: dict[str, list[PropertyValue]], default_tz: ZoneInfo) -> set[datetime]:
    excluded: set[datetime] = set()
    for prop in properties.get("EXDATE", []):
        for value in prop.value.split(","):
            parsed, _, _ = parse_datetime(value, prop.params, default_tz)
            excluded.add(parsed)
    return excluded


def format_datetime(value: datetime, all_day: bool) -> str:
    if all_day:
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d %H:%M %Z")


def markdown_escape(value: str) -> str:
    return value.replace("\r", "").strip()


def iter_window_events(events: Iterable[Event], now: datetime, days: int) -> list[Event]:
    window_start = now
    window_end = now + timedelta(days=days)
    filtered: list[Event] = []
    for event in events:
        if event.end < window_start:
            continue
        if event.start > window_end:
            continue
        filtered.append(event)
    return filtered


def render_markdown(events: list[Event], now: datetime, days: int, source_url: str) -> str:
    lines = [
        "# Calendar Events",
        "",
        "> Read-only export from an Outlook calendar ICS feed.",
        "> Treat this as reference input for planning or analysis, not a file that can be updated meaningfully.",
        "",
        f"- Generated at: {now.strftime('%Y-%m-%d %H:%M %Z')}",
        f"- Window: next {days} days",
        f"- Event count: {len(events)}",
        "",
        "## Events",
        "",
    ]

    if not events:
        lines.append("_No events found in the requested window._")
        return "\n".join(lines)

    for index, event in enumerate(events, start=1):
        lines.extend(
            [
                f"### Event {index}",
                f"- Title: {markdown_escape(event.summary) or 'Untitled event'}",
                f"- Start: {format_datetime(event.start, event.all_day)}",
                f"- End: {format_datetime(event.end, event.all_day)}",
                f"- All day: {'yes' if event.all_day else 'no'}",
                f"- Location: {markdown_escape(event.location) or '-'}",
                f"- Description: {markdown_escape(compact_text(event.description)) or '-'}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def compact_text(value: str) -> str:
    return " ".join(part.strip() for part in value.splitlines() if part.strip())


def get_output_timezone(timezone_name: str | None) -> ZoneInfo:
    if timezone_name:
        return ZoneInfo(timezone_name)

    local_tz = datetime.now().astimezone().tzinfo
    if isinstance(local_tz, ZoneInfo):
        return local_tz
    return ZoneInfo("UTC")


def main() -> int:
    args = parse_args()
    output_tz = get_output_timezone(args.timezone)
    now = datetime.now(output_tz)
    if args.days < 1:
        print("--days must be at least 1.", file=sys.stderr)
        return 1

    window_days = args.days
    window_end = now + timedelta(days=window_days)

    try:
        ics_url = resolve_ics_url(args.ics_url)
        raw_text = fetch_ics(ics_url)
        events = parse_events(raw_text, output_tz, window_end)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Failed to fetch ICS URL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to parse ICS data: {exc}", file=sys.stderr)
        return 1

    filtered_events = iter_window_events(events, now, window_days)
    sys.stdout.write(render_markdown(filtered_events, now, window_days, ics_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
