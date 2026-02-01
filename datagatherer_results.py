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
            "last_enriched_index": 0,
        }
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ================== FAILURE LOG ================== #

def log_failed_url(url: str):
    with open(FAILED_URLS_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

# ================== HTTP (RETRY SAFE) ================== #

def get_parsed_page(url: str):
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                FLARE_SOLVERR_URL,
                json=payload,
                timeout=70,
            )
            response.raise_for_status()

            data = response.json()
            if data.get("status") != "ok":
                raise RuntimeError(f"FlareSolverr bad status: {data}")

            return BeautifulSoup(
                data["solution"]["response"],
                "lxml",
            )

        except Exception as e:
            logging.warning(
                f"Attempt {attempt}/{MAX_RETRIES} failed for {url}: {e}"
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)

    logging.error(f"Permanent failure for {url}")
    log_failed_url(url)
    return None

# ================== TEAM CACHE ================== #

def _get_all_teams():
    if TEAM_MAP_FOR_RESULTS:
        return

    page = get_parsed_page("https://www.hltv.org/stats/teams?minMapCount=0")
    if not page:
        return

    for team in page.find_all("td", {"class": "teamCol-teams-overview"}):
        TEAM_MAP_FOR_RESULTS.append(
            {
                "id": converters.to_int(team.find("a")["href"].split("/")[-2]),
                "name": team.find("a").text.strip(),
            }
        )

def _findTeamId(name: str):
    _get_all_teams()
    for team in TEAM_MAP_FOR_RESULTS:
        if team["name"] == name:
            return team["id"]
    return None

# ================== DATE ================== #

def _month_to_number(name: str):
    if name == "Augu":
        name = "August"
    return datetime.datetime.strptime(name, "%B").month

# ================== RESULTS SCRAPER ================== #

def get_results(state):
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            try:
                results = json.load(f)
            except json.JSONDecodeError:
                results = []
    else:
        results = []

    existing_ids = {r["match-id"] for r in results if "match-id" in r}
    offset = state["results_offset"]

    while not time_exceeded() and offset <= MAX_RESULTS_OFFSET:
        logging.info(f"Results offset {offset}")

        page = get_parsed_page(f"https://www.hltv.org/results?offset={offset}")
        if not page:
            break

        sections = page.find_all("div", {"class": "results-holder"})
        if not sections:
            break

        new_found = False

        for section in sections:
            for res in section.find_all("div", {"class": "result-con"}):
                href = res.find("a", {"class": "a-reset"})["href"]
                match_id = converters.to_int(href.split("/")[-2])

                if match_id in existing_ids:
                    continue

                entry = {
                    "match-id": match_id,
                    "url": "https://hltv.org" + href,
                }

                headline = section.find("span", {"class": "standard-headline"})
                if headline:
                    txt = (
                        headline.text.replace("Results for ", "")
                        .replace("th", "")
                        .replace("rd", "")
                        .replace("st", "")
                        .replace("nd", "")
                    )
                    m, d, y = txt.split()
                    dt = datetime.datetime(
                        int(y),
                        _month_to_number(m),
                        int(d),
                        tzinfo=HLTV_ZONEINFO,
                    ).astimezone(LOCAL_ZONEINFO)
                    entry["date"] = dt.strftime("%Y-%m-%d")

                event = res.find("td", {"class": "event"}) or res.find(
                    "td", {"class": "placeholder-text-cell"}
                )
                entry["event"] = event.text.strip() if event else None

                teams = res.find_all("td", {"class": "team-cell"})
                if len(teams) == 2:
                    entry["team1"] = teams[0].text.strip()
                    entry["team2"] = teams[1].text.strip()
                    entry["team1-id"] = _findTeamId(entry["team1"])
                    entry["team2-id"] = _findTeamId(entry["team2"])

                    scores = res.find("td", {"class": "result-score"}).find_all("span")
                    entry["team1score"] = converters.to_int(scores[0].text)
                    entry["team2score"] = converters.to_int(scores[1].text)

                results.append(entry)
                existing_ids.add(match_id)
                new_found = True

        if not new_found:
            break

        offset += 100
        state["results_offset"] = offset
        save_state(state)
        time.sleep(1)

    return results

# ================== MATCH DETAILS ================== #

def parse_match_details(soup):
    data = {"format": "", "stage": "", "veto": [], "maps": []}

    maps_section = soup.find("div", class_="col-6 col-7-small")
    if not maps_section:
        return data

    veto_boxes = maps_section.find_all("div", class_="standard-box veto-box")
    for box in veto_boxes:
        text = box.get_text("\n").lower()
        if any(k in text for k in ["removed", "picked", "was left over"]):
            data["veto"] = [l.strip() for l in text.split("\n") if l.strip()]
            break

    for holder in maps_section.find_all("div", class_="mapholder"):
        name = holder.find("div", class_="mapname")
        data["maps"].append(
            {
                "map": name.text.strip() if name else "Unknown",
                "raw": holder.get_text(" ", strip=True),
            }
        )

    return data

# ================== ENRICH ================== #

def enrich_results(results, state):
    start_index = state.get("last_enriched_index", 0)

    for idx in range(start_index, len(results)):
        if time_exceeded():
            logging.warning("Time limit reached during enrichment")
            break

        match = results[idx]

        if match.get("maps"):
            state["last_enriched_index"] = idx + 1
            save_state(state)
            continue

        logging.info(
            f"Enriching match {match['match-id']} ({idx+1}/{len(results)})"
        )

        soup = get_parsed_page(match["url"])
        if soup:
            match.update(parse_match_details(soup))
        else:
            match["enrich_failed"] = True
            logging.warning(
                f"Failed to enrich match {match['match-id']}"
            )

        state["last_enriched_index"] = idx + 1
        save_state(state)
        time.sleep(0.5)

    return results

# ================== MAIN ================== #

def main():
    state = load_state()

    results = get_results(state)
    results = enrich_results(results, state)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4, ensure_ascii=False)

    save_state(state)
    logging.info("Run completed safely")

if __name__ == "__main__":
    main()
