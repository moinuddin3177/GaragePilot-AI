"""
GaragePilot AI: customer app.

Describe a car problem, get an AI pre-diagnosis, see the best nearby garages ranked by
distance, estimated cost and quality, and send the job to registered garages so they can
quote before you arrive.

Run:
    streamlit run app.py
"""

import os

import anthropic
import pandas as pd
import streamlit as st

from garagepilot import flow, notify, places
from garagepilot.diagnose import DiagnosisError, answer_diagnosis_question, diagnose
from garagepilot.models import Vehicle
from garagepilot.places import PlacesError
from garagepilot.store import Store, StoreError
from garagepilot.ui import esc

st.set_page_config(page_title="GaragePilot AI", page_icon="🚗", layout="wide")

SEVERITY_LABELS = {1: "Minor", 2: "Minor", 3: "Moderate", 4: "Serious", 5: "Dangerous"}
STATE_KEYS = ("vehicle", "symptoms", "photo", "address", "answers", "diagnosis", "origin", "garages", "lead_id", "notify_results")


@st.cache_resource
def get_store() -> Store:
    return Store()


@st.cache_resource
def server_anthropic_key() -> str | None:
    """The deployment's own key (from .env or hosting secrets), captured once at process
    startup so it can never be overwritten by something a later visitor types."""
    return os.environ.get("ANTHROPIC_API_KEY") or None


def diagnosis_client() -> anthropic.Anthropic | None:
    """A client for *this* diagnose() call: the server's key if one is configured, else the
    key this visitor typed (kept in their own session state, never shared or written to the
    server's environment). None means no key is available; diagnose() reports that clearly."""
    key = server_anthropic_key() or ss.get("session_anthropic_key")
    return anthropic.Anthropic(api_key=key) if key else None


store = get_store()
ss = st.session_state


def reset() -> None:
    for k in STATE_KEYS:
        ss.pop(k, None)
    st.query_params.clear()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Settings")
    if server_anthropic_key():
        st.caption("✅ Diagnosis is configured by this server.")
    else:
        st.text_input(
            "Anthropic API key",
            type="password",
            value="",
            placeholder="sk-ant-...",
            help="Used only for your own diagnosis, kept for this browser session only, "
            "and never saved on the server. Get one at console.anthropic.com",
            key="session_anthropic_key",
        )
        if not ss.get("session_anthropic_key"):
            st.warning("Enter your API key to enable diagnosis.")
    st.divider()
    st.caption(
        "Garages: **demo data** (no Google key set)" if places.is_fixture_mode() else "Garages: live Google Places"
    )
    st.caption(
        "Email: **dry-run** (printed to the server console)" if notify.is_dry_run() else "Email: sending via SMTP"
    )
    st.caption("Are you a garage? Open **Garage Portal** in the page list.")

st.title("🚗 GaragePilot AI")


# ---------------------------------------------------------------------------
# Quote view: shown once a request has been sent
# ---------------------------------------------------------------------------

def render_lead(lead_id: str) -> None:
    st.subheader("Your request is with the garages")
    st.caption("Garages see the AI diagnosis, not your contact details. Yours are shared only with the garage you choose.")

    for r in ss.get("notify_results", []):
        if not r.ok:
            st.warning(f"Could not notify {r.garage_name}: {r.error}")

    progress = store.lead_progress(lead_id)
    st.write(
        f"**{len(progress)} garage(s) notified:** "
        + ", ".join(f"{esc(p['name'])} ({p['status']})" for p in progress)
    )
    st.button("🔄 Refresh quotes", key="refresh")

    chosen = store.chosen_garage(lead_id)
    quotes = store.lead_quotes(lead_id)
    if not quotes:
        st.info("No quotes yet. Garages usually reply within a few hours. Press refresh to check.")
    for q in quotes:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{esc(q.garage_name)}** — quote **{esc(f'${q.price:,.0f}')}**")
            c1.caption(esc(" · ".join(x for x in (f"Earliest slot: {q.earliest_slot}" if q.earliest_slot else "", q.note) if x)))
            if chosen == q.place_id:
                phone = (store.get_garage(q.place_id) or {}).get("phone")
                st.success(f"You chose {esc(q.garage_name)}. They now have your contact details." + (f" Their phone: {esc(phone)}" if phone else ""))
            elif not chosen and c2.button("Choose this garage", key=f"choose_{q.place_id}"):
                try:
                    store.choose_garage(lead_id, q.place_id)
                except StoreError as exc:
                    st.error(str(exc))
                st.rerun()

    if st.button("Start a new request", key="new_request"):
        reset()
        st.rerun()


lead_id = st.query_params.get("lead") or ss.get("lead_id")
if lead_id and store.lead_exists(lead_id):
    render_lead(lead_id)
    st.stop()


# ---------------------------------------------------------------------------
# Step 1: describe the problem
# ---------------------------------------------------------------------------

st.caption("Tell us what's wrong. We'll diagnose it, find good garages nearby, and let them quote before you arrive.")

with st.form("intake"):
    c1, c2, c3, c4 = st.columns(4)
    make = c1.text_input("Make", placeholder="Honda", key="in_make")
    model = c2.text_input("Model", placeholder="Civic", key="in_model")
    year = c3.number_input("Year", min_value=1980, max_value=2027, value=2015, key="in_year")
    mileage = c4.number_input("Mileage (optional)", min_value=0, max_value=500_000, value=0, step=1000, key="in_mileage")
    symptoms = st.text_area(
        "What's going wrong?",
        placeholder="e.g. Grinding noise when I brake, and the pedal feels soft. Started two days ago.",
        key="in_symptoms",
    )
    photo = st.file_uploader(
        "Photo (optional): dashboard warning light, a leak, damage",
        type=["png", "jpg", "jpeg", "webp"],
        key="in_photo",
        help="Sent to Anthropic's AI to help diagnose the problem.",
    )
    address = st.text_input("Where are you? (address, area or zip code)", key="in_address")
    submitted = st.form_submit_button("Diagnose and find garages", type="primary")

if submitted:
    if not (make.strip() and model.strip() and symptoms.strip() and address.strip()):
        st.error("Please fill in the make, model, problem description and your location.")
    else:
        for k in STATE_KEYS:
            ss.pop(k, None)
        ss.vehicle = {"make": make.strip(), "model": model.strip(), "year": int(year), "mileage": int(mileage) or None}
        ss.symptoms, ss.address, ss.answers = symptoms.strip(), address.strip(), []
        ss.photo = (photo.getvalue(), photo.type) if photo else None
        try:
            with st.spinner("Diagnosing your car problem…"):
                ss.diagnosis = diagnose(Vehicle(**ss.vehicle), ss.symptoms, ss.answers, ss.photo, client=diagnosis_client())
            with st.spinner("Finding garages near you…"):
                ss.origin, ss.garages = flow.search(ss.address, store)
        except (DiagnosisError, PlacesError) as exc:
            st.error(str(exc))
            for k in STATE_KEYS:
                ss.pop(k, None)

if "diagnosis" not in ss:
    st.stop()


# ---------------------------------------------------------------------------
# Step 2: follow-up questions (only if the description was too vague)
# ---------------------------------------------------------------------------

d = ss.diagnosis

if d.follow_up_questions:
    st.subheader("A couple of quick questions")
    st.caption("This will make the diagnosis more accurate. The result below is provisional until you answer.")
    with st.form("followups"):
        round_no = len(ss.answers)
        replies = [st.text_input(q, key=f"fu_{round_no}_{i}") for i, q in enumerate(d.follow_up_questions)]
        if st.form_submit_button("Update diagnosis"):
            ss.answers = ss.answers + [(q, a.strip() or "Not sure") for q, a in zip(d.follow_up_questions, replies)]
            try:
                with st.spinner("Updating the diagnosis…"):
                    ss.diagnosis = diagnose(Vehicle(**ss.vehicle), ss.symptoms, ss.answers, ss.photo, client=diagnosis_client())
            except DiagnosisError as exc:
                st.error(str(exc))
            st.rerun()


# ---------------------------------------------------------------------------
# Step 3: diagnosis
# ---------------------------------------------------------------------------

st.subheader("Your pre-diagnosis")
if not d.safe_to_drive:
    st.error("🛑 **Do not drive this car.** Call a tow or a mobile mechanic. Driving it could be dangerous.")
elif d.severity >= 3:
    st.warning(f"⚠️ {SEVERITY_LABELS[d.severity]} issue. Get it looked at soon ({d.urgency.replace('_', ' ')}).")
else:
    st.success(f"✅ {SEVERITY_LABELS[d.severity]} issue. Safe to drive; fix it {d.urgency.replace('_', ' ')}.")

st.write(esc(d.summary))
st.dataframe(
    pd.DataFrame({"Possible cause": [c.cause for c in d.likely_causes], "Likelihood": [f"{c.probability:.0%}" for c in d.likely_causes]}),
    hide_index=True,
    use_container_width=True,
)
st.caption("AI estimate from your description. Only a mechanic can confirm the cause. Costs below are estimates, not quotes.")


# ---------------------------------------------------------------------------
# Step 3a: Q&A chatbot about the diagnosis
# ---------------------------------------------------------------------------

st.divider()
with st.expander("🪗 Ask AI flying accordion", expanded=False):
    if "diagnosis_qa_history" not in ss:
        ss.diagnosis_qa_history = []

    # Display chat history
    for msg in ss.diagnosis_qa_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    # Chat input
    if question := st.chat_input("Ask about your diagnosis...", key="diagnosis_qa_input"):
        # Add user message to history and display
        ss.diagnosis_qa_history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.write(question)

        # Get answer from Claude
        try:
            with st.spinner("Thinking…"):
                answer = answer_diagnosis_question(d, question, diagnosis_client())
            ss.diagnosis_qa_history.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.write(answer)
        except DiagnosisError as exc:
            st.error(f"Cannot answer: {str(exc)}")
        except Exception as exc:
            st.error(f"Unexpected error: {str(exc)}")
        st.rerun()


# ---------------------------------------------------------------------------
# Step 4: best garages
# ---------------------------------------------------------------------------

st.subheader("Best garages near you")
preset = st.radio(
    "What matters most?",
    options=list(flow.PRESET_LABELS),
    format_func=flow.PRESET_LABELS.get,
    horizontal=True,
    key="preset",
)
matches = flow.rank_matches(d, ss.garages, ss.origin, preset=preset)

if not matches:
    st.info(f"No garages found within {flow.SEARCH_RADIUS_KM:.0f} km. Try a different location.")
    st.stop()

st.map(pd.DataFrame({"lat": [m.garage.lat for m in matches], "lon": [m.garage.lng for m in matches]}))

for i, m in enumerate(matches, 1):
    g = m.garage
    with st.container(border=True):
        left, mid, right = st.columns([3, 2, 2])
        badge = " ✅ *On GaragePilot*" if g.registered else ""
        left.markdown(f"**{i}. {esc(g.name)}**{badge}")
        left.caption(esc(m.reason))
        mid.metric("Estimated cost", esc(f"${m.cost.low:,.0f} – ${m.cost.high:,.0f}"))
        right.metric("Distance", f"{m.distance_km:.1f} km")
        links = st.columns(4)
        if g.phone:
            links[0].write(f"📞 {g.phone}")
        links[1].link_button("Directions", f"https://www.google.com/maps/dir/?api=1&destination={g.lat},{g.lng}")
        if g.website:
            links[2].link_button("Website", g.website)


# ---------------------------------------------------------------------------
# Step 5: send the job to registered garages
# ---------------------------------------------------------------------------

st.subheader("Get quotes before you arrive")
registered = [m for m in matches if m.garage.registered]
if not registered:
    st.info("None of these garages have joined GaragePilot yet, so we can't send them your job. Call one directly using the details above.")
    st.stop()

st.write(f"**{len(registered)}** of these garages are on GaragePilot: " + ", ".join(esc(m.garage.name) for m in registered))
with st.form("send"):
    name = st.text_input("Your name", key="send_name")
    contact = st.text_input("Your phone or email", key="send_contact")
    consent = st.checkbox(
        "I agree to share my car problem, vehicle details and the AI diagnosis with these garages. "
        "My name and contact details are shared only with the garage I choose.",
        key="consent",
    )
    if st.form_submit_button("Send my issue to these garages", type="primary"):
        if not (name.strip() and contact.strip()):
            st.error("Please enter your name and a phone number or email so your chosen garage can reach you.")
        elif not consent:
            st.error("Please tick the consent box to send your request.")
        else:
            new_lead = store.create_lead(Vehicle(**ss.vehicle), ss.symptoms, d, matches, name, contact)
            ss.notify_results = notify.notify_lead(store, new_lead)
            ss.lead_id = new_lead
            st.query_params["lead"] = new_lead
            st.rerun()
