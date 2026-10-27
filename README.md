# Job application assistant

A local pipeline that discovers jobs, deduplicates and scores them, creates tailored materials, and applies only after a per-job approval in the review dashboard. The Markdown resume in `data/resume.md` is the source of truth for candidate claims.

## Setup

Use Python 3.11 or newer from the project directory:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp -n .env.example .env
cp -n preferences.example.yaml preferences.yaml
cp -n data/resume.example.md data/resume.md
playwright install chromium
```

On Ubuntu 22.04 without `python3.11`, use `uv` to create the environment with a current Python 3.11 release (no `sudo` needed):

```bash
python3 -m pip install --user --upgrade uv
~/.local/bin/uv venv --python 3.11 .venv
source .venv/bin/activate
~/.local/bin/uv pip install -e '.[dev]'
python --version
python -m playwright install chromium
```

Do not run the package installation if environment creation fails: `pip` would then use the system Python. Check `python --version` and `which python` after activating. [uv installation](https://docs.astral.sh/uv/getting-started/installation/), [uv managed Python](https://docs.astral.sh/uv/guides/install-python/).

Edit `preferences.yaml` for your profile, roles, locations, salary, blacklisted companies, thresholds, and source settings. Replace `data/resume.md` with your Markdown resume. Put API keys and OAuth paths in `.env`; never add credentials to YAML. Run `job-agent check-config` after editing. Personal configuration and resume files are ignored by Git.

## Run each phase

| Phase | Command | Focused check |
| --- | --- | --- |
| 1. Database and config | `job-agent check-config && job-agent init-db` | `pytest -q tests/test_storage.py` |
| 2. Discovery | `job-agent discover` | `pytest -q tests/test_sources.py` |
| 3. Scoring | `job-agent score` | `pytest -q tests/test_scoring.py` |
| 4. Tailoring | `job-agent tailor` | `pytest -q tests/test_tailoring.py` and inspect `data/artifacts/<job-id>/` |
| 5. Dashboard | `job-agent-dashboard` then open `http://127.0.0.1:8000` | Review queued jobs and use Edit, Approve, or Reject; `pytest -q tests/test_approval.py` |
| 6. Apply | `job-agent apply` in a desktop session | Inspect tracker status and screenshots under `data/artifacts/<job-id>/` |
| 7. Scheduler and digest | `job-agent run-once`, `job-agent digest`, or `job-agent scheduler` | Check terminal digest and tracker |

The package command runs from the project directory so `preferences.yaml` and the local dashboard resolve correctly. `job-agent run-once` runs discovery, scoring, tailoring, and digest. It never launches a browser or applies. `job-agent scheduler` runs that pipeline daily at 08:00 Asia/Kolkata. A cron alternative is `0 8 * * * cd /path/to/job-find-agent && .venv/bin/job-agent run-once` (set cron's timezone to Asia/Kolkata). Run `pytest -q` for all tests.

## Discovery credentials

### Gmail alerts

1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a project, then enable the **Gmail API** from **APIs & Services > Library**.
2. Open **Google Auth platform > Branding** and set an app name and support/contact email. For a personal Gmail account, choose **External** under **Audience**, keep **Testing**, and add your own Gmail address under **Test users**. Under **Data Access > Add or Remove Scopes**, add `https://www.googleapis.com/auth/gmail.readonly`.
3. Open **Google Auth platform > Clients > Create Client**, choose **Desktop app**, and download the JSON. Rename it to `data/google-oauth-client.json`. This file is ignored by Git. In `.env`, set `GMAIL_CLIENT_SECRET_FILE=data/google-oauth-client.json` and leave `GMAIL_TOKEN_FILE=data/oauth-token.json`.
4. Set `sources.gmail.enabled: true` in `preferences.yaml`. From the project directory and a desktop session, run `job-agent discover`. Your browser opens once for Google sign-in and consent; choose the Gmail account you listed as a test user. The saved token is reused on later runs.

The `gmail.readonly` scope grants read access to the mailbox, while the configured Gmail search limits which messages this app processes. It cannot send or modify messages. Google classifies the scope as restricted. With an External app in Testing, authorization (including a refresh token) normally expires after seven days, so the app will ask you to consent again. [Google Gmail quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python), [OAuth consent guide](https://developers.google.com/workspace/guides/configure-oauth-consent), [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [Testing limits](https://support.google.com/cloud/answer/15549945?hl=en).

The parser reads LinkedIn, Indeed, and Naukri alert emails. Email formats vary; extracted title, company, location, and URL should be checked in the review queue. An email's body is used as description when a full public posting is unavailable. [Gmail message listing guide](https://developers.google.com/workspace/gmail/api/guides/list-messages), [Gmail Python OAuth quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python).

### Public feeds

- **Greenhouse:** Add employer board tokens under `sources.greenhouse.board_tokens`, then enable the source. Its public job feed includes full post content when `content=true`. [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html).
- **Lever:** Add employer site slugs under `sources.lever.company_slugs`, then enable the source. The public postings API provides job descriptions. [Lever Postings API](https://github.com/lever/postings-api/blob/master/README.md).
- **Adzuna:** Get an app ID/key, set `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in `.env`, then enable it. Adzuna may provide only a description excerpt. [Adzuna API overview](https://developer.adzuna.com/overview).
- **Remote OK:** Its public feed needs no key, but it is disabled in the current email-only configuration. Credit and link back to Remote OK if you enable and reuse its data publicly. [Remote OK API FAQ](https://remoteok.com/faq).

Sources implement `JobSource.fetch_jobs() -> list[Job]` in `src/job_agent/sources/`. Add a new plugin there and register it in `sources/runner.py`. Feed failures are logged independently.

## Scoring and tailoring

Set `OPENAI_API_KEY` in `.env` to use the OpenAI API. The current local `.env` selects `gpt-6.1-sol` for higher-quality scoring and tailoring; the code fallback and `.env.example` use the less expensive `gpt-4o-mini`. GPT-6.1 Sol uses low reasoning effort with room in the output budget for its reasoning tokens. Model access depends on your API account. You do not need an Anthropic key. Company, location, and salary hard filters run before the model call. Scores use Structured Outputs plus local strict JSON validation and are retried once if invalid. Jobs with a score at or above `review_threshold` and no dealbreakers enter the queue; others remain `scored`. Without the OpenAI key, scoring and tailoring wait without changing job status. [OpenAI model](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [Reasoning token limits](https://developers.openai.com/api/docs/guides/reasoning), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

Tailoring asks the OpenAI model for a summary, an ordering of skills from `data/resume.md`, and a short cover letter. Each generated sentence must cite an exact quote from the source resume. The tailored PDF keeps the full categorized skills section, puts the most relevant categories first, and includes the portfolio URL transcribed from the original PDF. This is a grounding check, not a proof of semantic accuracy; read and edit the generated PDF and cover letter in the dashboard before approval. The per-job editable Markdown resume, resume PDF, cover letter text, and cover letter PDF are saved in `data/artifacts/<job-id>/`.

## Review and applications

The dashboard binds to localhost. Its review queue shows scores, reasons, missing skills, job links, and materials. The **All jobs** tracker shows every role with a separate application indicator, stage badge, dates, score, notes, search, and filters for **Not applied**, **Approved**, **Applied**, **To review**, and **Needs attention**. Results are shown 20 at a time. A job is shown as **Applied** only when a submission time is recorded; approval is clearly shown as **Approved · not applied**. The dashboard records an approval timestamp only when you click Approve. The applier selects only jobs whose status is `approved` **and** whose application row has that timestamp. `daily_apply_limit` defaults to 15. The headed browser runs only when you invoke `job-agent apply`; it uses ordinary Playwright without stealth or automated login. It fills standard fields on Greenhouse and Lever, stops on unknown required fields, login challenges, or CAPTCHAs, and saves screenshots. Custom question suggestions, when possible, are written to `answers_draft.txt` for your review, never inserted automatically.

LinkedIn explicitly disallows third-party activity automation, and Indeed prohibits automated Indeed Apply outside approved tooling. Naukri terms could not be verified from its site here. For these sites, use `job-agent open-manual <job-id>` after dashboard approval; the tracker displays the exact command and links to the tailored resume PDF and cover letter PDF. The command prints their local file paths so you can upload the PDFs or paste the cover letter text when the form asks. It opens the URL with a persistent browser profile, lets you log in and apply yourself, and records a screenshot only after you type `submitted` in the terminal. The program does not interact with their forms. [LinkedIn prohibited software guidance](https://www.linkedin.com/help/linkedin/answer/a1341387/prohibition-of-scraping-software?lang=en), [Indeed terms](https://www.indeed.com/legal?hl=en_US).

Greenhouse and Lever forms vary by employer, so some approved jobs will require manual attention. A detected submit click without a recognizable confirmation is marked `needs_attention`, not `applied`. Check the screenshot before updating any tracker record.

## Digest and tracker

`job-agent digest` prints the day's new matches, applied count, attention count, and follow-ups due seven days after application. Optional SMTP settings in `.env` send the same digest by email. The tracker at `/tracker` shows status, dates, notes, and screenshots; it lets you add notes and mark interviews or offers. The database, OAuth token, browser profile, and generated materials are ignored by Git.
