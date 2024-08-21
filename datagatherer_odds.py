import requests
from bs4 import BeautifulSoup
import pandas as pd
import logging
import json

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)

FLARE_SOLVERR_URL = "http://localhost:8191/v1"


def get_parsed_page(url):
    headers = {
        "referer": "https://www.hltv.org/betting/analytics",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }
    post_body = {"cmd": "request.get", "url": url, "maxTimeout": 60000}

    try:
        response = requests.post(FLARE_SOLVERR_URL, headers=headers, json=post_body)
        response.raise_for_status()
        json_response = response.json()
        if json_response.get("status") == "ok":
            html = json_response["solution"]["response"]
            return BeautifulSoup(html, "lxml")
    except requests.RequestException as e:
        logging.error(f"Error making HTTP request: {e}")
    return None


def get_odds():
    oddspage = get_parsed_page("http://www.hltv.org/betting/money")
    oddsdays = oddspage.find_all("div", {"class": "b-match-container"})
    matches_with_odds = []

    for odds in oddsdays:
        oddsDetails = odds.find_all("table", {"class": "bookmakerMatch"})

        for getOdds in oddsDetails:
            analytics = getOdds.find("a", {"class": "a-reset"})
            team1 = getOdds.find_all("div", {"class": "team-name"})[0].text
            team2 = getOdds.find_all("div", {"class": "team-name"})[1].text

            # b3651 = getOdds.find_all("td", {"class": "b-list-odds-provider-bet365"})
            # b3651 = b3651[0].text.strip() if b3651 else None
            # b3652 = getOdds.find_all("td", {"class": "b-list-odds-provider-bet365"})
            # b3652 = b3652[1].text.strip() if len(b3652) > 1 else None

            nordic1 = getOdds.find_all("td", {"class": "b-list-odds-provider-betsson"})
            nordic1 = nordic1[0].text.strip() if nordic1 else None
            nordic2 = getOdds.find_all("td", {"class": "b-list-odds-provider-betsson"})
            nordic2 = nordic2[1].text.strip() if len(nordic2) > 1 else None

            leovegas1 = getOdds.find_all(
                "td", {"class": "b-list-odds-provider-leovegas"}
            )
            leovegas1 = leovegas1[0].text.strip() if leovegas1 else None
            leovegas2 = getOdds.find_all(
                "td", {"class": "b-list-odds-provider-leovegas"}
            )
            leovegas2 = leovegas2[1].text.strip() if len(leovegas2) > 1 else None

            unibet1 = getOdds.find_all("td", {"class": "b-list-odds-provider-unibet"})
            unibet1 = unibet1[0].text.strip() if unibet1 else None
            unibet2 = getOdds.find_all("td", {"class": "b-list-odds-provider-unibet"})
            unibet2 = unibet2[1].text.strip() if len(unibet2) > 1 else None

            # Check if any odds are available (not empty or None)
            if any(
                odd
                for odd in [leovegas1, leovegas2, nordic1, nordic2, unibet1, unibet2]
            ):
                match_data = {
                    "team1": team1,
                    "team2": team2,
                    "leovegas1": leovegas1 if leovegas1 else "N/A",
                    "leovegas2": leovegas2 if leovegas2 else "N/A",
                    "nordic1": nordic1 if nordic1 else "N/A",
                    "nordic2": nordic2 if nordic2 else "N/A",
                    "unibet1": unibet1 if unibet1 else "N/A",
                    "unibet2": unibet2 if unibet2 else "N/A",
                    "href": analytics["href"],
                }
                matches_with_odds.append(match_data)

    return matches_with_odds


if __name__ == "__main__":
    matches = get_odds()

    # Write matches with odds to a JSON file
    with open("upcoming.json", "w") as outfile:
        json.dump(matches, outfile, indent=4)

    # Optional: Print to console for verification
    for match in matches:
        print(json.dumps(match, indent=4))
