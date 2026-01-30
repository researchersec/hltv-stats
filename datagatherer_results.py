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
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)

HLTV_COOKIE_TIMEZONE = "Europe/Copenhagen"
HLTV_ZONEINFO = zoneinfo.ZoneInfo(HLTV_COOKIE_TIMEZONE)
LOCAL_TIMEZONE_NAME = tzlocal.get_localzone_name()
LOCAL_ZONEINFO = zoneinfo.ZoneInfo(LOCAL_TIMEZONE_NAME)
FLARE_SOLVERR_URL = "http://localhost:8191/v1"

TEAM_MAP_FOR_RESULTS = []


def get_parsed_page(url):
    headers = {
        "referer": "https://www.hltv.org/stats",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }
    cookies = {"hltvTimeZone": HLTV_COOKIE_TIMEZONE}
    post_body = {"cmd": "request.get", "url": url, "maxTimeout": 60000}

    response = requests.post(FLARE_SOLVERR_URL, headers=headers, json=post_body)
    response.raise_for_status()

    data = response.json()
    if data.get("status") != "ok":
        return None

    return BeautifulSoup(data["solution"]["response"], "lxml")


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


def _findTeamId(team_name: str):
    _get_all_teams()
    for team in TEAM_MAP_FOR_RESULTS:
        if team["name"] == team_name:
            return team["id"]
    return None


def _pad(n):
    return str(n).zfill(2)


def _monthNameToNumber(name: str):
    if name == "Augu":
        name = "August"
    return datetime.datetime.strptime(name, "%B").month


def get_results(
    url="https://www.hltv.org/results",
    file_name="results.json",
    max_results=1000,
):
    logging.info("Loading existing results")

    if os.path.exists(file_name):
        with open(file_name, "r", encoding="utf-8") as f:
            try:
                results_list = json.load(f)
            except json.JSONDecodeError:
                results_list = []
    else:
        results_list = []

    # === CRITICAL: build index ===
    existing_match_ids = {
        r["match-id"] for r in results_list if "match-id" in r
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
                match_url = res.find("a", {"class": "a-reset"})["href"]
                match_id = converters.to_int(match_url.split("/")[-2])

                # === DEDUP GATE ===
                if match_id in existing_match_ids:
                    continue

                result = {
                    "match-id": match_id,
                    "url": "https://hltv.org" + match_url,
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
                    m, d, y = date_text.split()
                    date = datetime.datetime(
                        int(y),
                        _monthNameToNumber(m),
                        int(d),
                        tzinfo=HLTV_ZONEINFO,
                    ).astimezone(LOCAL_ZONEINFO)
                    result["date"] = date.strftime("%Y-%m-%d")
                else:
                    result["date"] = datetime.date.today().isoformat()

                event = res.find("td", {"class": "event"}) or res.find(
                    "td", {"class": "placeholder-text-cell"}
                )
                result["event"] = event.text.strip() if event else None

                teams = res.find_all("td", {"class": "team-cell"})
                if len(teams) == 2:
                    result["team1"] = teams[0].text.strip()
                    result["team2"] = teams[1].text.strip()
                    result["team1-id"] = _findTeamId(result["team1"])
                    result["team2-id"] = _findTeamId(result["team2"])

                    scores = res.find("td", {"class": "result-score"}).find_all("span")
                    result["team1score"] = converters.to_int(scores[0].text)
                    result["team2score"] = converters.to_int(scores[1].text)
                else:
                    result.update(
                        {
                            "team1": None,
                            "team2": None,
                            "team1-id": None,
                            "team2-id": None,
                            "team1score": None,
                            "team2score": None,
                        }
                    )

                results_list.append(result)
                existing_match_ids.add(match_id)
                new_entries_found = True

        if not new_entries_found:
            logging.info("Reached historical data. Stopping.")
            break

        offset += 100
        time.sleep(1)

    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(results_list, f, indent=4)

    logging.info(f"Saved {len(results_list)} total results")
    return results_list


if __name__ == "__main__":
    get_results(max_results=2000)
