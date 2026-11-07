# Valorant Analyst Discord Bot

A comprehensive Discord bot for Valorant competitive analysis, integrating with VDC.gg, Tracker.gg, and HenrikDev APIs to provide professional-grade scouting reports.

## Features

### Team Scouting
- **Full Team Reports** (`/scout_team`): VDC roster stats, map profiles, match history, anti-strat recommendations
- **Player Profiles** (`/scout_player`): Individual VDC season/combine stats, agent pools, role reads, pressure points
- **Map Veto Predictions** (`/map_veto`): Matchup-based ban/pick predictions with confidence scores
- **Postplant/Retake Exploits** (`/postplant_retake`): Phase-specific vulnerability analysis from custom matches

### Data Sources
- **VDC.gg** - Franchise pages, player pages, match logs, standings
- **HenrikDev API** - Custom match round data (plants, defuses, first contacts, retakes)
- **Tracker.gg** - Custom match URLs for roster validation
- **Riot Official** - Current competitive map pool (Patch 13.00)

### Commands

| Command | Description |
|---------|-------------|
| `/scout_team` | Complete team scouting dossier |
| `/scout_player` | Individual player deep dive |
| `/map_veto` | Matchup map ban/pick prediction |
| `/postplant_retake` | Phase exploit report |
| `/henrik_matches` | Fetch recent customs from HenrikDev |
| `/list_teams` | All known VDC team codes |
| `/current_pool` | Active competitive map pool |
| `/standings` | VDC S10 Expert standings |
| `/help_analyst` | Show all commands |

## Setup

### Prerequisites
- Python 3.10+
- Discord Bot Token
- HenrikDev API Token (for customs data)

### Installation

```bash
# 1. Install dependencies
cd bot
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your tokens

# 3. Run bot
python -m bot.main
```

Or use the launcher:
```bash
# From repo root
.\run_bot.bat
```

### Environment Variables (`.env`)

```env
DISCORD_TOKEN=your_discord_bot_token
HENRIKDEV_TOKEN=your_henrikdev_api_token
```

Get tokens from:
- Discord: https://discord.com/developers/applications
- HenrikDev: https://henrikdev.xyz/

## Usage Examples

### Scout a Team
```
/scout_team team_code:FV org_name:"Final Verdict" team_name:Paragon tier:expert season:10 tracker_url:"https://tracker.gg/valorant/profile/riot/BayHarborGooner%23Goonr/customs" players:"Ptrang#0110,Sixto#lgacy,yearn#love,BayHarborGooner#Goonr,Portent#FV1"
```

### Predict Map Veto
```
/map_veto team_a_code:FV team_a_org:"Final Verdict" team_a_name:Paragon team_b_code:L7 team_b_org:"Lucky 7" team_b_name:Blackjacks tier:expert season:10
```

### Postplant/Retake Exploit Report
```
/postplant_retake team_code:NB org_name:"Night Bloom" team_name:Eclipse tracker_url:"https://tracker.gg/valorant/profile/riot/oso%23shc/customs" players:"sukii#0010,oso#shc,HAIRYMONSTER34#Suppr,Meth Monkey#ball,Karitu#amo"
```

## Architecture

```
bot/
├── main.py              # Bot entry point, command definitions
├── requirements.txt     # Python dependencies
├── .env.example         # Environment template
└── services/            # (Planned) API service modules
```

The bot shells out to the existing PowerShell scripts (`Invoke-PostplantRetakeScout.ps1`, `Invoke-MapBanPrediction.ps1`) for heavy data collection, then formats results for Discord.

## Integration with Existing Workspace

The bot uses the same `TeamDirectory`, `KnownMaps`, and analysis logic as the PowerShell scripts. Generated reports are saved to `exports/` and also sent to Discord.

## VDC Team Codes

Use `/list_teams` to see all 16 Expert teams. Common codes:
- `FV` - Final Verdict Paragon
- `L7` - Lucky 7 Blackjacks
- `WS` - Wolfstara Equinox
- `FF` - Friendly Fire Execution
- `NB` - Night Bloom Eclipse
- `INF` - Inferno Eternal
- `CHA` - Chaos Theory Entropy
- `LG` - Light Garden Purple Iris

## Current Map Pool (Patch 13.00)

Ascent, Breeze, Haven, Lotus, Split, Summit, Sunset

*Removed: Bind, Fracture, Pearl*

## Development

### Adding New Commands
1. Add command function in `main.py`
2. Register with `@bot.tree.command()`
3. Use existing `run_powershell_script()` for PS integration
4. Format output with Discord embeds/markdown

### Extending Data Sources
Create new modules in `bot/services/` for:
- Direct VDC HTML parsing (replace PS scripts)
- Tracker.gg API integration
- VLR.gg match data
- Custom ML models for win prediction

## License

Internal tooling for competitive Valorant analysis.