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

# Set up logging configuration
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


def _get_all_teams():
    logging.info("Fetching all teams.")
    if not TEAM_MAP_FOR_RESULTS:
        teams = get_parsed_page("https://www.hltv.org/stats/teams?minMapCount=0")
        for team in teams.find_all(
            "td",
            {
                "class": ["teamCol-teams-overview"],
            },
        ):
            team = {
                "id": converters.to_int(team.find("a")["href"].split("/")[-2]),
                "name": team.find("a").text,
                "url": "https://hltv.org" + team.find("a")["href"],
            }
            TEAM_MAP_FOR_RESULTS.append(team)
        logging.info(f"Loaded {len(TEAM_MAP_FOR_RESULTS)} teams.")


def _findTeamId(teamName: str):
    logging.debug(f"Finding team ID for {teamName}.")
    _get_all_teams()
    for team in TEAM_MAP_FOR_RESULTS:
        if team["name"] == teamName:
            logging.debug(f"Found team ID for {teamName}: {team['id']}")
            return team["id"]
    logging.warning(f"Team ID for {teamName} not found.")
    return None


def _padIfNeeded(numberStr: str):
    return str(numberStr).zfill(2) if int(numberStr) < 10 else str(numberStr)


def _monthNameToNumber(monthName: str):
    logging.debug(f"Converting month name to number: {monthName}")
    if monthName == "Augu":
        monthName = "August"
    return datetime.datetime.strptime(monthName, "%B").month


def get_parsed_page(url):
    logging.info(f"Fetching page: {url}")
    headers = {
        "referer": "https://www.hltv.org/stats",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }
    cookies = {"hltvTimeZone": HLTV_COOKIE_TIMEZONE}
    post_body = {"cmd": "request.get", "url": url, "maxTimeout": 60000}

    try:
        response = requests.post(FLARE_SOLVERR_URL, headers=headers, json=post_body)
        response.raise_for_status()
        json_response = response.json()
        if json_response.get("status") == "ok":
            html = json_response["solution"]["response"]
            logging.info(f"Successfully fetched page: {url}")
            return BeautifulSoup(html, "lxml")
    except requests.RequestException as e:
        logging.error(f"Error making HTTP request: {e}")
    return None


def top5teams():
    logging.info("Fetching top 5 teams.")
    home = get_parsed_page("https://hltv.org/")
    teams = []
    for team in home.find_all(
        "div",
        {
            "class": ["col-box rank"],
        },
    ):
        team = {
            "id": _findTeamId(team.text[3:]),
            "name": team.text[3:],
            "url": "https://hltv.org" + team.find_all("a")[1]["href"],
        }
        teams.append(team)
    logging.info(f"Top 5 teams fetched: {teams}")
    return teams


def top30teams(url="https://www.hltv.org/ranking/teams/", file_name="ranking.json"):
    logging.info("Fetching top 30 teams.")
    if os.path.exists(file_name):
        with open(file_name, "r") as json_file:
            try:
                teamlist = json.load(json_file)
                logging.info(f"Loaded existing ranking from {file_name}.")
            except json.JSONDecodeError:
                teamlist = []
                logging.warning(f"Failed to load existing ranking from {file_name}.")
    else:
        teamlist = []
        logging.info("No existing ranking file found. Starting fresh.")

    page = get_parsed_page(url)
    teams = page.find("div", {"class": "ranking"})
    teamlist = []
    for team in teams.find_all("div", {"class": "ranked-team standard-box"}):
        newteam = {
            "name": team.find("div", {"class": "ranking-header"}).select(".name")[0].text.strip(),
            "rank": converters.to_int(
                team.select(".position")[0].text.strip(), regexp=True
            ),
            "rank-points": converters.to_int(
                team.find("span", {"class": "points"}).text, regexp=True
            ),
            "team-id": _findTeamId(
                team.find("div", {"class": "ranking-header"}).select(".name")[0].text.strip()
            ),
            "team-url": "https://hltv.org/team/"
            + team.find("a", {"class": "details moreLink"})["href"].split("/")[-1]
            + "/"
            + team.find("div", {"class": "ranking-header"}).select(".name")[0].text.strip(),
            "stats-url": "https://www.hltv.org"
            + team.find("a", {"class": "details moreLink"})["href"],
            "team-players": [],
        }
        for player_div in team.find_all("td", {"class": "player-holder"}):
            player = {}
            player["name"] = player_div.find("img", {"class": "playerPicture"})["title"]
            player["player-id"] = converters.to_int(
                player_div.select(".pointer")[0]["href"].split("/")[-2]
            )
            player["url"] = (
                "https://www.hltv.org" + player_div.select(".pointer")[0]["href"]
            )
            newteam["team-players"].append(player)
        teamlist.append(newteam)

    with open(file_name, "w") as json_file:
        json.dump(teamlist, json_file, indent=4)
        logging.info(f"Top 30 teams ranking saved to {file_name}.")

    return json.dumps(teamlist, indent=4)


def top_players():
    logging.info("Fetching top players.")
    page = get_parsed_page("https://www.hltv.org/stats")
    players = page.find_all("div", {"class": "col"})[0]
    playersArray = []
    for player in players.find_all("div", {"class": "top-x-box standard-box"}):
        playerObj = {}
        playerObj["country"] = player.find_all("img")[1]["alt"]
        buildName = player.find("img", {"class": "img"})["alt"].split("'")
        playerObj["name"] = buildName[0].rstrip() + buildName[2]
        playerObj["nickname"] = player.find("a", {"class": "name"}).text
        playerObj["rating"] = (
            player.find("div", {"class": "rating"}).find("span", {"class": "bold"}).text
        )
        playerObj["maps-played"] = (
            player.find("div", {"class": "average gtSmartphone-only"})
            .find("span", {"class": "bold"})
            .text
        )
        playerObj["url"] = "https://hltv.org" + player.find("a", {"class": "name"}).get("href")
        playerObj["id"] = converters.to_int(
            player.find("a", {"class": "name"}).get("href").split("/")[-2]
        )
        playersArray.append(playerObj)
    logging.info(f"Top players fetched: {playersArray}")
    return playersArray


def get_results(url="https://www.hltv.org/results", file_name="results.json", max_results=50000):
    logging.info("Starting to fetch results.")
    
    if os.path.exists(file_name):
        with open(file_name, "r") as json_file:
            try:
                results_list = json.load(json_file)
                logging.info(f"Loaded existing results from {file_name}.")
            except json.JSONDecodeError:
                logging.error(f"Failed to decode JSON from {file_name}. Starting with an empty list.")
                results_list = []
    else:
        results_list = []
        logging.info(f"No existing file found. Starting with an empty list.")

    offset = 0
    while offset < max_results:
        results_url = f"{url}?offset={offset}"
        logging.info(f"Fetching results from URL: {results_url}")
        
        results = get_parsed_page(results_url)
        
        if not results:
            logging.warning("No results fetched or failed to parse page. Stopping.")
            break

        pastresults = results.find_all("div", {"class": "results-holder"})
        if not pastresults:
            logging.info("No more results found on this page. Ending fetch loop.")
            break
        
        logging.info(f"Found {len(pastresults)} result sections to process.")

        for result in pastresults:
            resultDiv = result.find_all("div", {"class": "result-con"})

            for res in resultDiv:
                resultObj = {}

                resultObj["url"] = "https://hltv.org" + res.find(
                    "a", {"class": "a-reset"}
                ).get("href")

                resultObj["match-id"] = converters.to_int(
                    res.find("a", {"class": "a-reset"}).get("href").split("/")[-2]
                )

                if res.parent.find("span", {"class": "standard-headline"}):
                    dateText = (
                        res.parent.find("span", {"class": "standard-headline"})
                        .text.replace("Results for ", "")
                        .replace("th", "")
                        .replace("rd", "")
                        .replace("st", "")
                        .replace("nd", "")
                    )

                    dateArr = dateText.split()

                    dateTextFromArrPadded = (
                        _padIfNeeded(dateArr[2])
                        + "-"
                        + _padIfNeeded(_monthNameToNumber(dateArr[0]))
                        + "-"
                        + _padIfNeeded(dateArr[1])
                    )
                    dateFromHLTV = datetime.datetime.strptime(
                        dateTextFromArrPadded, "%Y-%m-%d"
                    ).replace(tzinfo=HLTV_ZONEINFO)
                    dateFromHLTV = dateFromHLTV.astimezone(LOCAL_ZONEINFO)

                    resultObj["date"] = dateFromHLTV.strftime("%Y-%m-%d")
                else:
                    dt = datetime.date.today()
                    resultObj["date"] = (
                        str(dt.day) + "/" + str(dt.month) + "/" + str(dt.year)
                    )

                if res.find("td", {"class": "placeholder-text-cell"}):
                    resultObj["event"] = res.find(
                        "td", {"class": "placeholder-text-cell"}
                    ).text
                elif res.find("td", {"class": "event"}):
                    resultObj["event"] = res.find("td", {"class": "event"}).text
                else:
                    resultObj["event"] = None

                if res.find_all("td", {"class": "team-cell"}):
                    resultObj["team1"] = (
                        res.find_all("td", {"class": "team-cell"})[0].text.strip()
                    )
                    resultObj["team1score"] = converters.to_int(
                        res.find("td", {"class": "result-score"})
                        .find_all("span")[0]
                        .text.strip()
                    )
                    resultObj["team1-id"] = _findTeamId(resultObj["team1"])
                    resultObj["team2"] = (
                        res.find_all("td", {"class": "team-cell"})[1].text.strip()
                    )
                    resultObj["team2-id"] = _findTeamId(resultObj["team2"])
                    resultObj["team2score"] = converters.to_int(
                        res.find("td", {"class": "result-score"})
                        .find_all("span")[1]
                        .text.strip()
                    )
                else:
                    resultObj["team1"] = None
                    resultObj["team1-id"] = None
                    resultObj["team1score"] = None
                    resultObj["team2"] = None
                    resultObj["team2-id"] = None
                    resultObj["team2score"] = None

                results_list.append(resultObj)

        logging.info(f"Processed offset {offset}. Total results collected: {len(results_list)}.")

        # Increase the offset for the next batch
        offset += 100  # Increment by 100 or any other value depending on how many results each page returns

        # Optionally: Add a sleep delay if necessary to avoid rate-limiting
        time.sleep(1)

    # Write the updated results list back to the file
    with open(file_name, "w", encoding='utf-8') as json_file:
        json.dump(results_list, json_file, indent=4)
        logging.info(f"Results successfully saved to {file_name}.")

    logging.info("Finished fetching results.")
    return json.dumps(results_list, indent=4)


def get_match_countdown(match_id):
    logging.info(f"Fetching match countdown for match ID: {match_id}.")
    url = "https://www.hltv.org/matches/" + str(match_id) + "/page"
    match_page = get_parsed_page(url)
    if not match_page:
        logging.error(f"Failed to fetch match page for match ID: {match_id}.")
        return None

    timeAndEvent = match_page.find("div", {"class": "timeAndEvent"})
    date = timeAndEvent.find("div", {"class": "date"}).text
    time = timeAndEvent.find("div", {"class": "time"}).text
    dateArr = (
        date.replace("th of", "")
        .replace("rd of", "")
        .replace("st of", "")
        .replace("nd of", "")
        .split()
    )
    dateTextFromArrPadded = (
        _padIfNeeded(dateArr[2])
        + "-"
        + _padIfNeeded(_monthNameToNumber(dateArr[1]))
        + "-"
        + _padIfNeeded(dateArr[0])
    )

    dateFromHLTV = datetime.datetime.strptime(
        dateTextFromArrPadded, "%Y-%m-%d"
    ).replace(tzinfo=HLTV_ZONEINFO)
    dateFromHLTV = dateFromHLTV.astimezone(LOCAL_ZONEINFO)

    date = dateFromHLTV.strftime("%Y-%m-%d")

    countdown = _generate_countdown(date, time)
    logging.info(f"Countdown for match ID {match_id}: {countdown}")
    return countdown


def _generate_countdown(date: str, time: str):
    logging.debug(f"Generating countdown for date {date} and time {time}.")
    timenow = (
        datetime.datetime.now().astimezone(LOCAL_ZONEINFO).strftime("%Y-%m-%d %H:%M")
    )
    deadline = date + " " + time
    currentTime = datetime.datetime.strptime(timenow, "%Y-%m-%d %H:%M")
    ends = datetime.datetime.strptime(deadline, "%Y-%m-%d %H:%M")
    if currentTime < ends:
        return str(ends - currentTime)
    return None


if __name__ == "__main__":
    logging.info("Script started.")
    try:
        top30teams()
        get_results(max_results=50000)  # Fetch results with a limit
        logging.info("Script finished successfully.")
    except Exception as e:
        logging.error(f"An error occurred: {e}")
