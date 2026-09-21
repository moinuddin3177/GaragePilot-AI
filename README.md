# GaragePilot AI

An AI agent that turns a customer's plain-language car problem into a diagnosis, finds nearby garages, ranks them by distance, estimated cost, quality and fit, and pre-briefs registered garages so they can quote before the customer arrives.

**Status:** scaffold only. See the implementation plan for the build order.

## Planned stack
Python, Claude (Anthropic API) for diagnosis, Google Places API for garage discovery, SQLite for garage/lead data, Streamlit for the customer app and garage portal, SMTP email for lead notifications.

## Setup
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # then fill in keys
```

## Limits
- Cost figures are estimates, not quotes.
- Not a substitute for professional inspection; safety-critical symptoms are routed to "don't drive / tow".
