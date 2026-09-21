# GaragePilot AI

An AI agent that turns a customer's plain-language car problem into a diagnosis, finds nearby garages, ranks them by distance, estimated cost, quality and fit, and pre-briefs registered garages so they can quote before the customer arrives.

- **Customers** describe the problem (optionally with a photo), see how serious it is, compare the best nearby garages, and get quotes without phoning around.
- **Garages** get a pre-diagnosed job (vehicle, symptoms, likely causes, severity, estimated value) instead of a cold walk-in, and reply with a quote and earliest slot.

## How it works

```
customer describes problem
        |
  Claude diagnosis  --(vague?)--> up to 2 follow-up questions per round
        |
  Google Places search  --> garages near the customer
        |
  deterministic ranking (distance, est. cost, rating, open now, skill fit, registered)
        |
  customer consents --> registered garages get an email + portal inbox entry
        |
  garages quote --> customer compares --> picks one --> only that garage gets the customer's contact details
```

Two garage tiers, because Google Places has no way to contact a business:

| | Discovered (from Google) | Registered (claimed listing) |
|---|---|---|
| Shown in results | yes | yes, with a ranking boost |
| Phone, directions, website | yes | yes |
| Receives the job and AI brief | no | yes (email + portal) |
| Can send a quote | no | yes |
| Uses its own labor rate and skills for cost/fit | no (estimated from Google's price level) | yes |

## Quick start

Requires Python 3.10 or newer (developed on 3.12).

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
streamlit run app.py
```

Open http://localhost:8501. Garages use the **Garage Portal** page in the sidebar (`/Garage_Portal`).

### Try it with no keys except Anthropic

With only `ANTHROPIC_API_KEY` set, the app runs in demo mode:

- **Garages** come from `data/places_fixture.json` (ten garages around lower Manhattan). Every address geocodes to that area.
- **Email** is printed to the terminal running Streamlit instead of being sent.

To see the whole loop, register a garage in the Garage Portal first (search any address, pick "Downtown Brake & Tire"), then submit a customer request. That garage will appear as "On GaragePilot", receive the job, and can quote it.

## Configuration

Set in `.env` (never commit it; it is git-ignored). The app sidebar also accepts the Anthropic key.

| Variable | Needed for | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Diagnosis | Get one at console.anthropic.com. API usage is billed separately from a Claude.ai subscription. |
| `GARAGEPILOT_MODEL` | Optional | Diagnosis model. Default `claude-sonnet-5`. Try `claude-opus-5` if diagnosis quality needs a boost. |
| `GOOGLE_MAPS_API_KEY` | Real garages | Leave blank for demo data. See below. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | Real email | Leave `SMTP_HOST` blank for dry-run. Uses STARTTLS (default port 587). |
| `APP_BASE_URL` | Email links | Where the app is reachable, e.g. `https://garagepilot.example.com`. Default `http://localhost:8501`. |
| `GARAGEPILOT_DB` | Optional | SQLite file path. Default `data/garagepilot.db`. |

### Google Maps key

1. In Google Cloud, create a project and **enable billing** (Places is a paid API; there is a free monthly allowance, check current pricing).
2. Enable **Places API (New)** and **Geocoding API**.
3. Create an API key and restrict it to those two APIs.
4. Put it in `.env` as `GOOGLE_MAPS_API_KEY`.

Each customer search makes one geocoding call and one Places call. Changing the priority selector re-ranks locally and makes no further calls.

## Project layout

```
app.py                     customer app (Streamlit)
pages/1_Garage_Portal.py   garage portal (Streamlit)
garagepilot/
  models.py                Diagnosis, Garage, Match, LeadBrief, Quote and the shared specialty list
  diagnose.py              Claude diagnosis (structured output), follow-up rounds, safety rules
  places.py                Google Places + Geocoding, demo-data fallback
  pricing.py               labor hours x rate + parts, as a low-high range
  matching.py              deterministic scoring and ranking, four priority presets
  flow.py                  search (paid, once) separated from ranking (free, instant)
  store.py                 SQLite: garages, leads, quotes, privacy rules
  notify.py                lead and registration emails, dry-run mode
  ui.py                    markdown/LaTeX escaping for prices and untrusted text
data/places_fixture.json   demo garages
tests/                     pytest suite
```

## Design notes

- **Ranking is plain arithmetic, not an LLM.** Each result carries a per-factor breakdown, so it is explainable and testable. Claude only produces the diagnosis. Ratings with few reviews are pulled toward the average, so a 5.0 from 3 reviews doesn't beat a 4.7 from 300.
- **Safety rules live in code, not just the prompt.** Severity 4 or 5 always means "do not drive, immediate", whatever the model said.
- **Privacy is enforced in the data layer.** A garage's view of a lead never contains the customer's name or contact until the customer chooses that garage. Lead emails contain none either. The customer's photo and description are sent to Anthropic for diagnosis; their contact details are not.
- **Only what garages tell us is stored.** For Google data we keep just the `place_id`; coordinates, ratings and hours are fetched fresh on each search, in line with Google's caching terms. Garage tokens are stored as hashes.

## Tests

```bash
python -m pytest -q
```

The suite needs no API keys and makes no network calls. It covers ranking, pricing, Places parsing (against mocked responses), diagnosis (against a fake client), the store and its privacy rules, email (dry-run and mocked SMTP), and both Streamlit pages end to end using Streamlit's `AppTest`.

## Known limits

Read these before putting it in front of real customers.

- **Garage claims are not verified.** Anyone can register any listing that is not already claimed. A squatter would receive AI briefs for that garage and, if a customer chose them, the customer's contact details. Add an ownership check first (for example a callback to the phone number on the Google listing, or a verification email to the business's domain).
- **Sign-in is a single emailed/shown token,** not a full account system: no password reset, no expiry, no multiple staff users. Anyone holding the token can act as the garage.
- **Costs are estimates, not quotes.** They come from the AI's labor-hours and parts ranges times a labor rate. Registered garages use their declared rate; others use a regional default (`DEFAULT_LABOR_RATE` in `pricing.py`, in USD) scaled by Google's price level. Real quotes from garages are the source of truth.
- **The diagnosis is a pre-diagnosis.** The AI cannot inspect the car. It flags dangerous symptoms conservatively, but it is not a substitute for a professional inspection.
- **Live services are lightly exercised.** Diagnosis, Places and SMTP are tested against fakes and mocks. Run a few real scenarios with real keys (for example "grinding noise when braking", "check-engine light and rough idle", "AC not cold") before relying on the output.
- **Single-region defaults.** Currency is USD and distances are in km; the demo data is New York.
- **Not built yet:** customer accounts, a "job completed" step and reviews, payments, SMS/WhatsApp alerts, and rate limiting on the public app.
