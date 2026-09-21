"""
GaragePilot AI: garage portal.

Garages claim their Google listing, set their skills and labor rate, then receive
pre-diagnosed jobs from nearby customers and reply with a quote.
"""

import streamlit as st

from garagepilot import notify, places
from garagepilot.models import SPECIALTIES
from garagepilot.places import PlacesError
from garagepilot.store import Store, StoreError
from garagepilot.ui import esc

st.set_page_config(page_title="GaragePilot Garage Portal", page_icon="🔧", layout="wide")


@st.cache_resource
def get_store() -> Store:
    return Store()


store = get_store()
ss = st.session_state

st.title("🔧 Garage Portal")


# ---------------------------------------------------------------------------
# Signed out: sign in or register
# ---------------------------------------------------------------------------

if "garage_id" not in ss:
    tab_in, tab_reg = st.tabs(["Sign in", "Register your garage"])

    with tab_in:
        with st.form("signin"):
            token = st.text_input("Access token", type="password", help="Shown when you registered and emailed to you.")
            if st.form_submit_button("Sign in", type="primary"):
                gid = store.garage_id_for_token(token)
                if gid:
                    ss.garage_id = gid
                    st.rerun()
                else:
                    st.error("That token wasn't recognised.")

    with tab_reg:
        st.write("Find your garage on Google Maps, tell us what you're good at, and start receiving pre-diagnosed jobs.")
        with st.form("find"):
            where = st.text_input("Your garage's address or area", key="reg_where")
            if st.form_submit_button("Search"):
                try:
                    origin = places.geocode(where)
                    ss.reg_options = places.search_garages(origin[0], origin[1], radius_km=5)
                except PlacesError as exc:
                    st.error(str(exc))

        options = ss.get("reg_options", [])
        if options:
            chosen = st.selectbox(
                "Select your garage",
                options,
                format_func=lambda g: f"{g.name}" + ("  (already claimed)" if store.get_garage(g.place_id) else ""),
                key="reg_choice",
            )
            with st.form("register"):
                email = st.text_input("Email for job alerts", key="reg_email")
                phone = st.text_input("Phone (shown to customers who choose you)", value=chosen.phone or "", key="reg_phone")
                skills = st.multiselect("What do you specialise in?", SPECIALTIES, key="reg_skills")
                rate = st.number_input("Labor rate per hour ($)", min_value=20.0, max_value=500.0, value=100.0, step=5.0, key="reg_rate")
                if st.form_submit_button("Register", type="primary"):
                    try:
                        new_token = store.register_garage(chosen.place_id, chosen.name, email, skills, rate, phone or None)
                    except StoreError as exc:
                        st.error(str(exc))
                    else:
                        try:
                            notify.send_registration_email(email, chosen.name, new_token)
                        except notify.NotifyError as exc:
                            st.warning(f"Registered, but the confirmation email failed: {exc}")
                        ss.garage_id = chosen.place_id
                        ss.new_token = new_token
                        st.rerun()
    st.stop()


# ---------------------------------------------------------------------------
# Signed in
# ---------------------------------------------------------------------------

gid = ss.garage_id
garage = store.get_garage(gid)
if garage is None:  # registration removed underneath us
    ss.pop("garage_id", None)
    st.rerun()

top_l, top_r = st.columns([4, 1])
top_l.subheader(esc(garage["name"]))
if top_r.button("Sign out"):
    for k in ("garage_id", "new_token", "reg_options"):
        ss.pop(k, None)
    st.rerun()

if "new_token" in ss:
    st.success("You're registered. Save your access token now; it's how you sign in, and it won't be shown again.")
    st.code(ss.new_token)

with st.expander("Your details"):
    with st.form("settings"):
        s_email = st.text_input("Email for job alerts", value=garage["email"])
        s_phone = st.text_input("Phone", value=garage["phone"] or "")
        s_skills = st.multiselect("Specialties", SPECIALTIES, default=garage["specialties"])
        s_rate = st.number_input("Labor rate per hour ($)", min_value=20.0, max_value=500.0, value=float(garage["labor_rate"] or 100.0), step=5.0)
        if st.form_submit_button("Save"):
            try:
                store.update_garage(gid, email=s_email, phone=s_phone, specialties=s_skills, labor_rate=s_rate)
                st.success("Saved.")
            except StoreError as exc:
                st.error(str(exc))

briefs = store.garage_leads(gid)
st.subheader("Incoming jobs")
if not briefs:
    st.info("No jobs yet. When a nearby customer sends a request that matches you, it will appear here and be emailed to you.")
    st.stop()


def label(b) -> str:
    v = b.vehicle
    return f"{v.year} {v.make} {v.model} — severity {b.diagnosis.severity}/5 — {b.status} ({b.created_at[:10]})"


ids = [b.lead_id for b in briefs]
wanted = st.query_params.get("lead")
by_id = {b.lead_id: b for b in briefs}
selected = st.selectbox(
    "Job",
    ids,
    index=ids.index(wanted) if wanted in ids else 0,
    format_func=lambda i: label(by_id[i]),
)

brief = store.lead_for_garage(selected, gid, mark_viewed=True)
d, v = brief.diagnosis, brief.vehicle

with st.container(border=True):
    st.markdown(f"### {v.year} {esc(v.make)} {esc(v.model)}" + (f" · {v.mileage:,} miles" if v.mileage else ""))
    c1, c2, c3 = st.columns(3)
    c1.metric("Severity", f"{d.severity}/5")
    c2.metric("Drivable?", "Yes" if d.safe_to_drive else "No, may need towing")
    c3.metric("Distance", f"{brief.distance_km:.1f} km")
    st.write(f"**Customer says:** {esc(brief.symptoms)}")
    st.write(f"**AI summary:** {esc(d.summary)}")
    st.write("**Likely causes:** " + "; ".join(f"{esc(c.cause)} ({c.probability:.0%})" for c in d.likely_causes))
    st.write(f"**Skills needed:** {', '.join(d.required_specialties) or 'general'}")
    st.caption(esc(f"Estimated job value at your rate: ${brief.est_cost.low:,.0f} to ${brief.est_cost.high:,.0f} (AI estimate, not a quote)."))

chosen_by_customer = store.chosen_garage(selected)
if brief.chosen:
    st.success(f"The customer chose you. Contact: **{esc(brief.customer_name)}**, {esc(brief.customer_contact)}")
elif chosen_by_customer:
    st.info("The customer chose another garage for this job.")
elif brief.status == "declined":
    st.info("You declined this job.")
else:
    st.subheader("Your quote" if not brief.quote else "Your quote (you can revise it)")
    with st.form(f"quote_{selected}"):
        price = st.number_input("Price ($)", min_value=0.0, value=float(brief.quote.price if brief.quote else round(brief.est_cost.mid)), step=10.0)
        slot = st.text_input("Earliest slot", value=brief.quote.earliest_slot if brief.quote else "", placeholder="e.g. Tomorrow 9am")
        note = st.text_area("Note to customer (optional)", value=brief.quote.note if brief.quote else "", placeholder="What's included, parts, warranty…")
        send, decline = st.columns(2)
        do_send = send.form_submit_button("Send quote", type="primary")
        do_decline = decline.form_submit_button("Decline job")
        if do_send or do_decline:
            try:
                if do_send:
                    store.submit_quote(selected, gid, price, slot, note)
                else:
                    store.decline_lead(selected, gid)
            except StoreError as exc:
                st.error(str(exc))
            else:
                st.rerun()
