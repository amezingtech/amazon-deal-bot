# Amazon.in Deal Bot — Free Telegram Automation

Posts fresh Amazon India deals to **your** Telegram channel with **your** affiliate tag,
fully automatically, for **₹0/month**.

**How it works:** every 30 minutes, GitHub Actions (free) runs `bot.py`, which reads
the public web previews (`t.me/s/...`) of popular Indian deal channels, picks the
freshest Amazon.in deals, rebuilds each link as
`https://www.amazon.in/dp/ASIN?tag=YOUR-TAG`, and posts it to your channel with the
product image via the free Telegram Bot API.

```
[deal channels] --> bot.py (filter + dedup + your tag) --> [your channel]
                        ^
                        +-- runs free on GitHub Actions cron
```

---

## Setup (one time, ~15 minutes)

### 1. Create the Telegram bot
1. Open Telegram, message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, choose a name and username
3. Copy the **bot token** (looks like `1234567:AAE...`)

### 2. Add the bot to YOUR channel
Channel settings → Administrators → **Add admin** → your new bot.
(For a public channel, also give the bot *Post messages* permission — default is fine.)

### 3. Find your channel ID
- **Public channel:** just use `@yourchannelname`
- **Private channel:** post any message in the channel, then open
  `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` in a browser
  and look for `"chat":{"id":-100xxxxxxxxxx}` — use that `-100...` number.

### 4. Put this project on GitHub
Create a new repository on github.com (a **public** repo gives you unlimited free
Action minutes; private also works but is capped at 2000 min/month — if private,
change the cron in `.github/workflows/post-deals.yml` from `*/30 * * * *` to `0 * * * *`).

Then either upload these files via the GitHub web UI, or:

```bash
cd amazon-deal-bot
git init
git add .
git commit -m "deal bot"
git branch -M main
git remote add origin https://github.com/YOUR-NAME/YOUR-REPO.git
git push -u origin main
```

### 5. Add your secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret** (×3):

| Secret name  | Value                                      |
|--------------|--------------------------------------------|
| `BOT_TOKEN`  | the token from step 1                      |
| `CHANNEL_ID` | `@yourchannel` or `-100xxxxxxxxxx`         |
| `AMAZON_TAG` | your Associates tracking ID, e.g. `mytag-21` (just the ID, no URL) |

### 6. Test it
Repo → **Actions** tab → *Post Amazon deals* → **Run workflow** → check your channel.

That's it — it now runs itself every 30 minutes.

---

## Customize (`config.json`)

| Setting              | What it does                                                              |
|----------------------|---------------------------------------------------------------------------|
| `sources`            | Telegram channels to monitor. Any **public** channel works: open `t.me/s/<name>` in a browser — if messages load, you can add it. |
| `priority_sources`   | Channels whose deals always post first when there's competition for slots |
| `short_links`        | `true` = post TinyURL short links (amzn.to can't be created by bots — Amazon keeps it SiteStripe-only). Your affiliate tag lives inside the short link either way; set `false` for full amazon.in links. |
| `max_posts_per_run`  | Cap per run (5 × 48 runs/day; dedup keeps actual volume much lower)        |
| `max_deal_age_hours` | Only repost deals posted within this many hours (keeps content fresh)      |
| `dedup_days`         | Don't repost the same product (ASIN) for this many days                   |
| `block_keywords`     | Skip messages containing these (other stores, giveaways, etc.)            |
| `shortlink_domains`  | Short-link domains that get resolved to find the ASIN                     |

## Test locally

```bash
pip install -r requirements.txt
python bot.py --dry-run   # prints deals without posting; no token needed
python bot.py             # actually posts (needs the 3 env vars above)
```

## Notes & good practice

- **Keep the affiliate disclosure** in posts (Amazon Associates requirement — it's in
  `config.json`).
- **Add your Telegram channel** to your Amazon Associates account (Storefronts / website
  list) so Amazon knows where your traffic comes from.
- **Don't scrape amazon.in pages** for deals — Amazon bans affiliate accounts for that.
  This bot only reads deal *communities* and links to Amazon, which is the safe pattern.
- GitHub disables scheduled workflows after 60 days with **no repo activity** — the
  automatic `posted.json` commits keep it alive. If it ever stops, just open a PR or
  push any commit to re-enable.
- After your Associates account makes 3 sales you can apply for the free
  **Product Advertising API** for real-time price data — a nice future upgrade.
