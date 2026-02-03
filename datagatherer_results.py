import os
import json
import time
import datetime
import logging
import requests
from bs4 import BeautifulSoup
from python_utils import converters
import zoneinfo
import tzlocal

# ================== CONFIG ================== #

MAX_RUNTIME_SECONDS = 60 * 5
MAX_RESULTS_OFFSET = 100

STATE_FILE = "scrape_state.json"
RESULTS_FILE = "results.json"
FAILED_URLS_FILE = "failed_urls.json"

MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 2

HLTV_COOKIE_TIMEZONE = "Europe/Copenhagen"
HLTV_ZONEINFO = zoneinfo.ZoneInfo(HLTV_COOKIE_TIMEZONE)
LOCAL_ZONEINFO = zoneinfo.ZoneInfo(tzlocal.get_localzone_name())

FLARE_SOLVERR_URL = "http://localhost:8191/v1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

START_TIME = None

# ================== TIME GUARD ================== #

def time_exceeded():
    return (time.time() - START_TIME) >= MAX_RUNTIME_SECONDS

# ================== STATE ================== #

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "known_match_ids": {},
            "enriched_match_ids": {},
            "failed_match_ids": {}
        }
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
        state.setdefault("known_match_ids", {})
        state.setdefault("enriched_match_ids", {})
        state.setdefault("failed_match_ids", {})
        return state

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ================== HTTP ================== #

def get_parsed_page(url):
    payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(FLARE_SOLVERR_URL, json=payload, timeout=70)
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "ok":
                raise RuntimeError("FlareSolverr non-ok")
            return BeautifulSoup(data["solution"]["response"], "lxml")
        except Exception as e:
            logging.warning(f"{attempt}/{MAX_RETRIES} failed for {url}: {e}")
            time.sleep(RETRY_SLEEP_SECONDS)

    with open(FAILED_URLS_FILE, "a") as f:
        f.write(url + "\n")
    return None

# ================== TEAM CACHE ================== #

TEAM_MAP = {}

def load_teams():
    if TEAM_MAP:
        return

    soup = get_parsed_page("https://www.hltv.org/stats/teams?minMapCount=0")
    if not soup:
        return

    for team in soup.find_all("td", class_="teamCol-teams-overview"):
        a = team.find("a")
        if not a:
            continue
        TEAM_MAP[a.text.strip()] = converters.to_int(a["href"].split("/")[-2])

def team_id(name):
    load_teams()
    return TEAM_MAP.get(name)

# ================== DATE ================== #

def month_to_number(name):
    if name == "Augu":
        name = "August"
    return datetime.datetime.strptime(name, "%B").month

# ================== SCRAPE RESULTS ================== #

def scrape_latest_results(state):
    results = []
    known_ids = set(state["known_match_ids"].keys())

    offset = 0
    while offset <= MAX_RESULTS_OFFSET and not time_exceeded():
        logging.info(f"Scraping offset {offset}")
        page = get_parsed_page(f"https://www.hltv.org/results?offset={offset}")
        if not page:
            break

        stop = False

        for section in page.find_all("div", class_="results-holder"):
            headline = section.find("span", class_="standard-headline")
            date_str = None
            if headline:
                txt = headline.text.replace("Results for ", "")
                for s in ["th", "rd", "st", "nd"]:
                    txt = txt.replace(s, "")
                try:
                    m, d, y = txt.split()
                    dt = datetime.datetime(
                        int(y),
                        month_to_number(m),
                        int(d),
                        tzinfo=HLTV_ZONEINFO
                    ).astimezone(LOCAL_ZONEINFO)
                    date_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    pass

            for res in section.find_all("div", class_="result-con"):
                a = res.find("a", class_="a-reset")
                if not a:
                    continue

                match_id = converters.to_int(a["href"].split("/")[-2])
                if match_id in known_ids:
                    stop = True
                    break

                entry = {
                    "match-id": match_id,
                    "url": "https://hltv.org" + a["href"],
                    "date": date_str
                }

                event = res.find("td", class_="event") or res.find("td", class_="placeholder-text-cell")
                entry["event"] = event.text.strip() if event else None

                teams = res.find_all("td", class_="team-cell")
                if len(teams) == 2:
                    entry["team1"] = teams[0].text.strip()
                    entry["team2"] = teams[1].text.strip()
                    entry["team1-id"] = team_id(entry["team1"])
                    entry["team2-id"] = team_id(entry["team2"])

                score_td = res.find("td", class_="result-score")
                if score_td:
                    spans = score_td.find_all("span")
                    if len(spans) == 2:
                        entry["team1score"] = converters.to_int(spans[0].text)
                        entry["team2score"] = converters.to_int(spans[1].text)

                results.append(entry)
                state["known_match_ids"][str(match_id)] = True

        save_state(state)

        if stop:
            logging.info("Reached known match, stopping scrape")
            break

        offset += 100
        time.sleep(1)

    return results

# ================== MATCH DETAILS ================== #

def parse_match_details(soup):
    data = {"format": "", "stage": "", "veto": [], "maps": []}

    maps_section = soup.find("div", class_="col-6 col-7-small")
    if not maps_section:
        return data

    for box in maps_section.find_all("div", class_="standard-box veto-box"):
        text = box.get_text("\n", strip=True)
        lines = [l for l in text.split("\n") if l]
        if lines:
            data["format"] = lines[0]
            if len(lines) > 1:
                data["stage"] = lines[1].lstrip("* ")

        if any(k in text.lower() for k in ["removed", "picked", "left over"]):
            data["veto"] = lines

    for holder in maps_section.find_all("div", class_="mapholder"):
        map_name = holder.find("div", class_="mapname")
        results = holder.find("div", class_="results")
        if not results:
            continue

        def parse_team(node):
            return {
                "name": node.find("div", class_="results-teamname").text.strip(),
                "score": node.find("div", class_="results-team-score").text.strip(),
                "status": "won" if "won" in node.get("class", []) else "lost"
            }

        half = results.find("div", class_="results-center-half-score")

        data["maps"].append({
            "map": map_name.text.strip() if map_name else "Unknown",
            "team1": parse_team(results.find("div", class_="results-left")),
            "team2": parse_team(results.find("span", class_="results-right")),
            "half_scores": half.text.strip() if half else "",
            "status": "played" if half else "not_played"
        })

    return data

# ================== ENRICH ================== #

def enrich_matches(matches, state):
    for match in matches:
        if time_exceeded():
            break

        mid = str(match["match-id"])
        if state["enriched_match_ids"].get(mid):
            continue

        soup = get_parsed_page(match["url"])
        if not soup:
            state["failed_match_ids"][mid] = True
            save_state(state)
            continue

        match.update(parse_match_details(soup))
        state["enriched_match_ids"][mid] = True
        save_state(state)
        time.sleep(0.2)

# ================== MAIN ================== #

def main():
    global START_TIME
    START_TIME = time.time()

    state = load_state()
    results = scrape_latest_results(state)

    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = []

    all_results = results + existing
    enrich_matches(all_results, state)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    save_state(state)
    logging.info("Done")

if __name__ == "__main__":
    main()
