"""Remote dashboard for the SDR weather-satellite ground station.

Reads passes and images from Supabase (read-only public key) and shows them.
Run locally:  streamlit run dashboard/app.py
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from supabase import create_client

LOCAL_TZ = "Asia/Riyadh"

st.set_page_config(page_title="SDR Weather Satellite Ground Station",
                   page_icon="🛰️", layout="wide")


# -------------------------------------------------------------------- data
@st.cache_resource
def get_client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


@st.cache_data(ttl=60, show_spinner="Loading passes…")
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    sb = get_client()
    passes = pd.DataFrame(
        sb.table("passes").select("*").order("pass_time", desc=True).limit(1000).execute().data)
    images = pd.DataFrame(
        sb.table("images").select("*").order("created_at", desc=True).limit(5000).execute().data)
    if not passes.empty:
        passes["pass_time"] = (pd.to_datetime(passes["pass_time"], utc=True)
                               .dt.tz_convert(LOCAL_TZ))
        passes["date"] = passes["pass_time"].dt.date
    return passes, images


def fmt_time(ts) -> str:
    return ts.strftime("%d %b %Y, %H:%M")


def show_images(imgs: pd.DataFrame, columns: int = 3) -> None:
    cols = st.columns(columns)
    for i, (_, img) in enumerate(imgs.iterrows()):
        with cols[i % columns]:
            st.image(img.get("thumb_url") or img["public_url"], use_container_width=True)
            size = f"{img['size_bytes'] / 1e6:.1f} MB" if pd.notna(img.get("size_bytes")) else ""
            dims = (f"{int(img['width'])}×{int(img['height'])}"
                    if pd.notna(img.get("width")) and pd.notna(img.get("height")) else "")
            st.caption(f"**{img['product']}**  \n{' · '.join(x for x in (dims, size) if x)}")
            st.link_button("Open full resolution", img["public_url"],
                           use_container_width=True)


# ------------------------------------------------------------------ layout
st.title("🛰️ SDR Weather Satellite Ground Station")
st.caption("Jazan University · College of Engineering and Computer Science · "
           "Low-cost reception of Meteor-M2 weather images")

if "SUPABASE_URL" not in st.secrets or "SUPABASE_KEY" not in st.secrets:
    st.error("Supabase secrets are missing. Add SUPABASE_URL and SUPABASE_KEY "
             "to .streamlit/secrets.toml (local) or the app's Secrets (Streamlit Cloud).")
    st.stop()

try:
    passes, images = load_data()
except Exception as exc:
    st.error(f"Could not reach the database: {exc}")
    st.stop()

if passes.empty:
    st.info("No passes uploaded yet. Run the uploader on the station PC after the "
            "next satellite pass and the images will appear here.")
    st.stop()

# Sidebar filters
with st.sidebar:
    st.header("Filters")
    sats = sorted(passes["satellite"].dropna().unique())
    chosen_sats = st.multiselect("Satellite", sats, default=sats)
    dmin, dmax = passes["date"].min(), passes["date"].max()
    picked = st.date_input("Date range", value=(dmin, dmax), min_value=dmin, max_value=dmax)
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.caption(f"Times shown in {LOCAL_TZ} (UTC+3).")

start, end = (picked if isinstance(picked, tuple) and len(picked) == 2 else (dmin, dmax))
view = passes[passes["satellite"].isin(chosen_sats)
              & (passes["date"] >= start) & (passes["date"] <= end)]
view_images = images[images["pass_id"].isin(view["id"])] if not images.empty else images

# Key numbers
c1, c2, c3, c4 = st.columns(4)
c1.metric("Passes received", len(view))
c2.metric("Images decoded", len(view_images))
c3.metric("Latest pass", fmt_time(view["pass_time"].max()) if not view.empty else "—")
stored = view_images["size_bytes"].sum() / 1e6 if not view_images.empty else 0
c4.metric("Data stored", f"{stored:.0f} MB")

if view.empty:
    st.warning("No passes match the selected filters.")
    st.stop()

tab_latest, tab_browse, tab_log = st.tabs(["Latest pass", "Browse passes", "Reception log"])

with tab_latest:
    latest = view.iloc[0]
    st.subheader(f"{latest['satellite']} — {fmt_time(latest['pass_time'])}")
    freq = f"{latest['frequency_mhz']} MHz" if pd.notna(latest.get("frequency_mhz")) else "—"
    st.caption(f"Frequency: {freq} · Station: {latest.get('station') or '—'}")
    imgs = view_images[view_images["pass_id"] == latest["id"]]
    if imgs.empty:
        st.info("This pass has no images yet.")
    else:
        show_images(imgs.sort_values("product"))

with tab_browse:
    labels = {row["id"]: f"{fmt_time(row['pass_time'])} — {row['satellite']}"
              for _, row in view.iterrows()}
    pass_id = st.selectbox("Pass", list(labels), format_func=labels.get)
    imgs = view_images[view_images["pass_id"] == pass_id]
    st.caption(f"{len(imgs)} image(s)")
    if imgs.empty:
        st.info("This pass has no images yet.")
    else:
        show_images(imgs.sort_values("product"))

with tab_log:
    st.subheader("Passes per day")
    per_day = view.groupby("date").size().rename("Passes")
    st.bar_chart(per_day)

    st.subheader("All passes")
    counts = (view_images.groupby("pass_id").size().rename("Images")
              if not view_images.empty else pd.Series(dtype=int, name="Images"))
    table = view.merge(counts, left_on="id", right_index=True, how="left")
    table["Images"] = table["Images"].fillna(0).astype(int)
    table["Time"] = table["pass_time"].dt.strftime("%Y-%m-%d %H:%M")
    table = table.rename(columns={"satellite": "Satellite", "frequency_mhz": "Frequency (MHz)",
                                  "station": "Station"})
    cols = ["Time", "Satellite", "Frequency (MHz)", "Images", "Station"]
    st.dataframe(table[cols], hide_index=True, use_container_width=True)
    st.download_button("Download log (CSV)", table[cols].to_csv(index=False).encode("utf-8"),
                       "reception_log.csv", "text/csv")
