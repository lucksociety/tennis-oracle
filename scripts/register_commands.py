"""Register the global /tennis slash command for the Discord application.

Usage:  DISCORD_BOT_TOKEN=... python scripts/register_commands.py [--guild GUILD_ID]
The token is read from the environment only and never printed. --guild registers instantly in one server
(global commands can take a few minutes to appear)."""
import json
import os
import sys
import urllib.request

APP_ID = os.environ.get("DISCORD_APPLICATION_ID", "1564848519913930784")
COMMANDS = [{
    "name": "tennis", "type": 1,
    "description": "Luck Society Tennis Oracle: today's slate - winners, win %, score predictions, Jupiter best bets",
    "integration_types": [0, 1], "contexts": [0, 1, 2],
    "options": [
        {"type": 3, "name": "date", "description": "Slate date YYYY-MM-DD (America/New_York). Default: today", "required": False},
        {"type": 3, "name": "tour", "description": "Tour filter", "required": False,
         "choices": [{"name": "All", "value": "all"}, {"name": "ATP", "value": "atp"}, {"name": "WTA", "value": "wta"}]},
    ],
}]


def main():
    tok = os.environ.get("DISCORD_BOT_TOKEN")
    if not tok: sys.exit("DISCORD_BOT_TOKEN not set")
    guild = sys.argv[sys.argv.index("--guild") + 1] if "--guild" in sys.argv else None
    url = f"https://discord.com/api/v10/applications/{APP_ID}" + (f"/guilds/{guild}" if guild else "") + "/commands"
    req = urllib.request.Request(url, data=json.dumps(COMMANDS).encode(), method="PUT",
                                 headers={"Authorization": f"Bot {tok}", "Content-Type": "application/json",
                                          "User-Agent": "DiscordBot (https://github.com/lucksociety/tennis-oracle, 1.0)"})
    try:
        with urllib.request.urlopen(req) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:400]}")
    for c in out:
        print(f"registered /{c['name']} id={c['id']} version={c['version']} scope={'guild ' + guild if guild else 'global'}")


if __name__ == "__main__":
    main()
