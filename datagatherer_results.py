import os
import json
import time
import datetime
import logging
import re
from bs4 import BeautifulSoup
from python_utils import converters
import requests
import zoneinfo
import tzlocal

# ================== CONFIG ================== #

MAX_RUNTIME_SECONDS = 60 * 300
MAX_RESULTS_OFFSET = 23000

STATE_FILE = "scrape_state.json"
RESULTS_FILE = "results.json"
FAILED_URLS_FILE = "failed_urls.json"

MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 2

HLTV_COOKIE_TIMEZONE = "Europe/Copenhagen"
HLTV_ZONEINFO = zoneinfo.ZoneInfo(HLTV_COOKIE_TIMEZONE)
LOCAL_ZONEINFO = zoneinfo.ZoneInfo(tzlocal.get_localzone_name())

FLARE_SOLVERR_URL = "http://localhost:8191/v1"

START_TIME = time.time()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

TEAM_MAP_FOR_RESULTS = []

# ================== TIME GUARD ================== #

def time_exceeded():
    return (time.time() - START_TIME) >= MAX_RUNTIME_SECONDS

# ================== STATE ================== #

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "results_offset": 0,
            "enriched_match_ids": {}
        }
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
        state.setdefault("enriched_match_ids", {})
        return state

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ================== FAILURE LOG ================== #

def log_failed_url(url):
    with open(FAILED_URLS_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

# ================== HTTP ================== #

def get_parsed_page(url):
    payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(FLARE_SOLVERR_URL, json=payload, timeout=70)
            r.raise_for_status()
            data = r.json()

            if data.get("status") != "ok":
                raise RuntimeError("FlareSolverr returned non-ok status")

            return BeautifulSoup(data["solution"]["response"], "lxml")

        except Exception as e:
            logging.warning(f"{attempt}/{MAX_RETRIES} failed for {url}: {e}")
            time.sleep(RETRY_SLEEP_SECONDS)

    log_failed_url(url)
    return None

# ================== TEAM CACHE ================== #

def _get_all_teams():
    if TEAM_MAP_FOR_RESULTS:
        return

    page = get_parsed_page("https://www.hltv.org/stats/teams?minMapCount=0")
    if not page:
        return

    for team in page.find_all("td", class_="teamCol-teams-overview"):
        TEAM_MAP_FOR_RESULTS.append({
            "id": converters.to_int(team.find("a")["href"].split("/")[-2]),
            "name": team.find("a").text.strip(),
        })

def _findTeamId(name):
    _get_all_teams()
    for t in TEAM_MAP_FOR_RESULTS:
        if t["name"] == name:
            return t["id"]
    return None

# ================== DATE ================== #

def _month_to_number(name):
    if name == "Augu":
        name = "August"
    return datetime.datetime.strptime(name, "%B").month

# ================== RESULTS ================== #

def get_results(state):
    results = []
    existing_ids = set()
    offset = state["results_offset"]

    while offset <= MAX_RESULTS_OFFSET and not time_exceeded():
        logging.info(f"Results offset {offset}")
        page = get_parsed_page(f"https://www.hltv.org/results?offset={offset}")
        if not page:
            break

        for section in page.find_all("div", class_="results-holder"):
            for res in section.find_all("div", class_="result-con"):
                href = res.find("a", class_="a-reset")["href"]
                match_id = converters.to_int(href.split("/")[-2])

                if match_id in existing_ids:
                    continue

                entry = {
                    "match-id": match_id,
                    "url": "https://hltv.org" + href,
                }

                headline = section.find("span", class_="standard-headline")
                if headline:
                    txt = headline.text.replace("Results for ", "")
                    for s in ["th", "rd", "st", "nd"]:
                        txt = txt.replace(s, "")
                    m, d, y = txt.split()
                    dt = datetime.datetime(
                        int(y),
                        _month_to_number(m),
                        int(d),
                        tzinfo=HLTV_ZONEINFO,
                    ).astimezone(LOCAL_ZONEINFO)
                    entry["date"] = dt.strftime("%Y-%m-%d")

                event = res.find("td", class_="event") or res.find(
                    "td", class_="placeholder-text-cell"
                )
                entry["event"] = event.text.strip() if event else None

                teams = res.find_all("td", class_="team-cell")
                if len(teams) == 2:
                    entry["team1"] = teams[0].text.strip()
                    entry["team2"] = teams[1].text.strip()
                    entry["team1-id"] = _findTeamId(entry["team1"])
                    entry["team2-id"] = _findTeamId(entry["team2"])

                    scores = res.find("td", class_="result-score").find_all("span")
                    entry["team1score"] = converters.to_int(scores[0].text)
                    entry["team2score"] = converters.to_int(scores[1].text)

                results.append(entry)
                existing_ids.add(match_id)

        offset += 100
        state["results_offset"] = offset
        save_state(state)
        time.sleep(1)

    return results

# ================== MATCH DETAILS ================== #

def parse_match_details(soup):
    data = {"format": "", "stage": "", "veto": [], "maps": []}

    maps_section = soup.find("div", class_="col-6 col-7-small")
    format_boxes = maps_section.find_all("div", class_="standard-box veto-box")

    for box in format_boxes:
        format_text = box.find("div", class_="padding preformatted-text")
        if format_text:
            lines = [l.strip() for l in format_text.text.split("\n") if l.strip()]
            data["format"] = lines[0] if lines else ""
            if len(lines) > 1:
                data["stage"] = lines[1].lstrip("* ").strip()

    for box in format_boxes:
        veto_div = box.find("div", class_="padding")
        if veto_div:
            veto_text = veto_div.text.lower()
            if any(k in veto_text for k in ["removed", "picked", "was left over"]):
                data["veto"] = [
                    step.text.strip()
                    for step in veto_div.find_all("div")
                    if step.text.strip()
                ]
                break

    map_holders = maps_section.find_all("div", class_="mapholder")
    for map_holder in map_holders:
        map_data = {}

        map_name_div = map_holder.find("div", class_="mapname")
        map_data["map"] = map_name_div.text.strip() if map_name_div else "Unknown"

        results = map_holder.find("div", class_="results")
        if not results:
            continue

        team1 = results.find("div", class_="results-left")
        team2 = results.find("span", class_="results-right")

        def parse_team(team):
            return {
                "name": team.find("div", class_="results-teamname").text.strip(),
                "score": team.find("div", class_="results-team-score").text.strip(),
                "status": "won" if "won" in team.get("class", []) else "lost",
            }

        half = results.find("div", class_="results-center-half-score")
        half_score = half.text.strip() if half else ""

        map_data["team1"] = parse_team(team1)
        map_data["team2"] = parse_team(team2)
        map_data["half_scores"] = half_score
        map_data["status"] = "played" if half_score else "not_played"

        data["maps"].append(map_data)

    return data

# ================== PLAYER STATS ================== #

def parse_player_stats(soup):
    stats_by_map = {}

    matchstats = soup.find("div", class_="matchstats")
    if not matchstats:
        return stats_by_map

    map_tabs = matchstats.select(".stats-menu-link .dynamic-map-name-full")
    map_names = [
        m.text.strip() for m in map_tabs
        if m.text.strip().lower() != "all maps"
    ]

    tables = matchstats.find_all("table", class_="totalstats")
    table_index = 2

    for map_name in map_names:
        stats_by_map[map_name] = {"team1": [], "team2": []}

        for team_key in ("team1", "team2"):
            if table_index >= len(tables):
                break

            table = tables[table_index]
            table_index += 1

            for row in table.find_all("tr")[1:]:
                nick = row.find("span", class_="player-nick")
                if not nick:
                    continue

                cells = row.find_all("td")
                if len(cells) < 9:
                    continue

                stats_by_map[map_name][team_key].append({
                    "name": nick.text.strip(),
                    "kd": cells[1].text.strip(),
                    "adr": cells[4].text.strip(),
                    "kast": cells[6].text.strip(),
                    "rating": cells[8].text.strip(),
                })

    return stats_by_map

# ================== ENRICH ================== #

def enrich_results(results, state):
    enriched_ids = state["enriched_match_ids"]

    for match in results:
        if time_exceeded():
            logging.warning("Time limit reached during enrichment")
            break

        match_id = str(match["match-id"])
        if enriched_ids.get(match_id):
            continue

        logging.info(f"Enriching match {match_id}")

        soup = get_parsed_page(match["url"])
        if not soup:
            match["enrich_failed"] = True
            save_state(state)
            continue

        match.update(parse_match_details(soup))

        player_stats = parse_player_stats(soup)
        for m in match.get("maps", []):
            m["players"] = player_stats.get(
                m["map"], {"team1": [], "team2": []}
            )

        enriched_ids[match_id] = True
        save_state(state)
        time.sleep(0.1)

    return results

# ================== MAIN ================== #

def main():
    state = load_state()
    results = get_results(state)
    results = enrich_results(results, state)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4, ensure_ascii=False)

    save_state(state)
    logging.info("Run completed successfully")

if __name__ == "__main__":
    main()
