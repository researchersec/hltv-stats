import os
import json
import time
import datetime
import logging
from bs4 import BeautifulSoup
from python_utils import converters
import requests
import zoneinfo
import tzlocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s -%(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)

HLTV_COOKIE_TIMEZONE = "Europe/Copenhagen"
HLTV_ZONEINFO = zoneinfo.ZoneInfo(HLTV_COOKIE_TIMEZONE)
LOCAL_ZONEINFO = zoneinfo.ZoneInfo(tzlocal.get_localzone_name())
FLARE_SOLVERR_URL = "http://localhost:8191/v1"

TEAM_MAP_FOR_RESULTS = []


# ------------------ HTTP ------------------ #

def get_parsed_page(url: str):
    headers = {
        "referer": "https://www.hltv.org",
        "user-agent": "Mozilla/5.0",
    }

    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000,
    }

    response = requests.post(FLARE_SOLVERR_URL, json=payload, headers=headers)
    response.raise_for_status()

    data = response.json()
    if data.get("status") != "ok":
        return None

    return BeautifulSoup(data["solution"]["response"], "lxml")


# ------------------ TEAM CACHE ------------------ #

def _get_all_teams():
    if TEAM_MAP_FOR_RESULTS:
        return

    page = get_parsed_page("https://www.hltv.org/stats/teams?minMapCount=0")
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


# ------------------ DATE HELPERS ------------------ #

def _month_to_number(name: str):
    if name == "Augu":
        name = "August"
    return datetime.datetime.strptime(name, "%B").month


# ------------------ RESULTS SCRAPER ------------------ #

def get_results(
    url="https://www.hltv.org/results",
    file_name="results.json",
    max_results=1000,
):
    logging.info("Loading existing results")

    if os.path.exists(file_name):
        with open(file_name, "r", encoding="utf-8") as f:
            try:
                results = json.load(f)
            except json.JSONDecodeError:
                results = []
    else:
        results = []

    # Dedup index
    existing_match_ids = {
        r["match-id"] for r in results if "match-id" in r
    }

    offset = 0

    while offset < max_results:
        logging.info(f"Fetching offset {offset}")
        page = get_parsed_page(f"{url}?offset={offset}")

        if not page:
            break

        sections = page.find_all("div", {"class": "results-holder"})
        if not sections:
            break

        new_entries_found = False

        for section in sections:
            for res in section.find_all("div", {"class": "result-con"}):
                href = res.find("a", {"class": "a-reset"})["href"]
                match_id = converters.to_int(href.split("/")[-2])

                if match_id in existing_match_ids:
                    continue

                entry = {
                    "match-id": match_id,
                    "url": "https://hltv.org" + href,
                }

                headline = section.find("span", {"class": "standard-headline"})
                if headline:
                    date_text = (
                        headline.text.replace("Results for ", "")
                        .replace("th", "")
                        .replace("rd", "")
                        .replace("st", "")
                        .replace("nd", "")
                    )
                    month, day, year = date_text.split()
                    dt = datetime.datetime(
                        int(year),
                        _month_to_number(month),
                        int(day),
                        tzinfo=HLTV_ZONEINFO,
                    ).astimezone(LOCAL_ZONEINFO)
                    entry["date"] = dt.strftime("%Y-%m-%d")
                else:
                    entry["date"] = datetime.date.today().isoformat()

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
                else:
                    entry.update(
                        dict.fromkeys(
                            [
                                "team1",
                                "team2",
                                "team1-id",
                                "team2-id",
                                "team1score",
                                "team2score",
                            ]
                        )
                    )

                results.append(entry)
                existing_match_ids.add(match_id)
                new_entries_found = True

        if not new_entries_found:
            logging.info("No new matches found — stopping early")
            break

        offset += 100
        time.sleep(1)

    # UTF-8 SAFE WRITE
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(
            results,
            f,
            indent=4,
            ensure_ascii=False,
        )

    logging.info(f"Saved {len(results)} total results")
    return results


# ------------------ ENTRYPOINT ------------------ #

if __name__ == "__main__":
    get_results(max_results=500)
