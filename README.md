# LinkedIn Job Leads

A Python command-line workflow for collecting hiring posts, extracting explicit contact emails, and ranking posts against a PDF resume. An optional daily runner stores progress in SQLite and can deliver reports through Telegram.

## Features

- Selenium-based post collection using a dedicated Chrome profile.
- PDF text/skill extraction and Gemini-assisted classification and ranking.
- JSON/CSV exports and extraction of emails present in the post text.
- Daily-run persistence, deduplication, resumable scoring, a file lock, and delivery history.
- Tests covering key-pool handling, daily candidates, deduplication, and delivery behavior.

This is a personal automation tool, not an official LinkedIn integration. Selectors can break when LinkedIn changes its pages. It does not bypass login or guarantee complete results. Review matches before acting on them and use only data and accounts you are authorized to access.

## Setup

Requires Python 3.10+, Google Chrome, and a LinkedIn account for live collection.

~~~sh
python -m venv .venv
~~~

Activate .venv (Windows PowerShell: .venv/Scripts/Activate.ps1; macOS/Linux: source .venv/bin/activate), then:

~~~sh
python -m pip install -r requirements.txt
~~~

Copy .env.example to .env and configure your own Gemini key/model pair for scoring. No credentials are needed for the unit tests. Never commit your resume, Chrome profile, collected posts, or reports.

## Collect posts

Launch Chrome with a **dedicated** profile and remote debugging, then sign into LinkedIn in that window. Example for Windows PowerShell, from the repository root:

~~~powershell
& "$env:ProgramFiles/Google/Chrome/Application/chrome.exe" --remote-debugging-port=9222 --user-data-dir="$PWD/chrome_profile"
~~~

Keep that browser open and run:

~~~sh
python main.py --query "hiring backend engineer" --remote-port 9222 --max-posts 30 --skip-score
python main.py --skip-scrape --resume resume.pdf
~~~

The first command saves posts.json; the second scores saved posts and writes results.json. For the full pipeline, omit --skip-score and pass --resume during collection. Use python main.py --help for date filters and the legacy --email-mode CSV workflow.

## Daily runner

Set RESUME_PATH and DAILY_CHROME_PROFILE to your own files. The daily runner starts headless Chrome itself: close any browser currently using that profile. Run locally without messaging:

~~~sh
python daily.py --check --no-telegram
python daily.py --no-telegram
~~~

The check validates local configuration only; it does not test network access or login. --score-only retries saved posts. Exit code 2 means partial work remains; progress is retained for retry.

The default daily policy targets non-internship Node.js/MERN roles in Ahmedabad or remote roles open to India. Customize QUERIES and POLICY in daily.py for your own search. Scheduling is external; this script does not install a scheduled task.

For Telegram, set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and omit --no-telegram. That mode **sends reports** to the configured chat and records delivery. The local-only mode exports reports without marking Telegram delivery.

Local-only exports still enter the deduplication history, so those emails are not automatically re-sent by a later Telegram run. Use a separate DAILY_DATA_DIR when experimenting with delivery modes.

## Tests

~~~sh
python -m unittest discover -s tests -v
~~~

These are offline tests; live LinkedIn collection, Gemini quality, and Telegram delivery require separate checks with your own configuration.

## Architecture and files

main.py coordinates collection and scoring; scrape_posts.py and scraper.py handle browser collection; resume_parser.py extracts PDF text; gemini_scorer.py handles scoring; daily.py manages persistence and reporting. tests/ covers the persistent workflow and key pool. Examples of secrets belong only in .env.example as placeholders.

## Data boundaries

Scoring sends post/resume content to the configured model provider. Telegram mode sends reports to Telegram. Browser cookies remain in the local profile; source control excludes profiles, resumes, keys, databases, exports, and logs. Do not treat model-generated classifications as verified job facts.

## Review results

See [publication validation](VALIDATION.md) for the checks performed, fixes, and unverified integrations.
