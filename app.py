import math
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import streamlit as st
import streamlit.components.v1 as components
import folium
from streamlit_folium import st_folium

# -----------------------------------------------------------------------------
# Konfiguration & Konstanten
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Inclusive BS – Barrierefreiheits-Guide TU Braunschweig",
    page_icon="♿",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Supabase & Admin Zugangsdaten aus Streamlit Secrets laden
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

BS_CENTER_LAT = 52.2735
BS_CENTER_LON = 10.5295
DEFAULT_ZOOM = 16
CLUSTER_RADIUS_METERS = 10.0
DEFAULT_REQUIRED_CONFIRMATIONS = 3
SUBMIT_COOLDOWN_SECONDS = 20
LOCAL_TZ = ZoneInfo("Europe/Berlin")

KATEGORIEN = [
    "Rollstuhlgerecht / Rampe",
    "Aufzug defekt / außer Betrieb",
    "Auditive / Visuelle Hilfe",
    "Ruheort",
    "Temporärer Ruhe-/Arbeitsraum",
    "Sonstiges Hindernis"
]

KATEGORIE_ICONS_TEXT = {
    "Rollstuhlgerecht / Rampe": "♿ Rollstuhlgerecht / Rampe",
    "Aufzug defekt / außer Betrieb": "🚨 Aufzug defekt / außer Betrieb",
    "Auditive / Visuelle Hilfe": "🔊 Auditive / Visuelle Hilfe",
    "Ruheort": "🌿 Ruheort",
    "Temporärer Ruhe-/Arbeitsraum": "🕒 Temporärer Ruhe-/Arbeitsraum",
    "Sonstiges Hindernis": "⚠️ Sonstiges Hindernis"
}

STATUS_OPTIONEN = [
    "Gemeldet",
    "In Prüfung",
    "Behoben / Barrierefrei"
]

BEWERTUNGS_TAGS = [
    "Flache Steigung",
    "Automatiktür",
    "Breit genug",
    "Steil / Hilfe nötig",
    "Reizarm / Leise",
    "Sitzgelegenheit",
    "Gut ausgeschildert",
    "Steckdosen vorhanden",
    "WLAN verfügbar"
]

KATEGORIE_STYLES = {
    "Rollstuhlgerecht / Rampe": {"color": "green", "icon": "wheelchair"},
    "Aufzug defekt / außer Betrieb": {"color": "red", "icon": "exclamation-triangle"},
    "Auditive / Visuelle Hilfe": {"color": "blue", "icon": "volume-up"},
    "Ruheort": {"color": "purple", "icon": "leaf"},
    "Temporärer Ruhe-/Arbeitsraum": {"color": "darkgreen", "icon": "clock-o"},
    "Sonstiges Hindernis": {"color": "orange", "icon": "info-sign"}
}

TU_CAMPUS_PINS = [
    {"name": "Altgebäude (Hauptgebäude TU BS)", "adresse": "Pockelsstraße 4", "lat": 52.27345, "lon": 10.52765},
    {"name": "Okerhochhaus", "adresse": "Pockelsstraße 14", "lat": 52.27278, "lon": 10.52746},
    {"name": "Universitätsbibliothek (UB)", "adresse": "Universitätsplatz 1", "lat": 52.27385, "lon": 10.52923},
    {"name": "Forum-Gebäude / Hochhaus", "adresse": "Universitätsplatz 2", "lat": 52.27349, "lon": 10.52972},
    {"name": "Auditorium Maximum (Audimax)", "adresse": "Universitätsplatz 3", "lat": 52.27297, "lon": 10.52970},
    {"name": "Biozentrum", "adresse": "Spielmannstraße 7", "lat": 52.27425, "lon": 10.53077},
    {"name": "Architektur-Pavillons", "adresse": "Pockelsstraße 3", "lat": 52.27340, "lon": 10.52625},
    {"name": "Mensa 1", "adresse": "Katharinenstraße 1", "lat": 52.27465, "lon": 10.52611},
    {"name": "Informatikzentrum (IZ)", "adresse": "Mühlenpfordtstraße 23", "lat": 52.27323, "lon": 10.52514},
]

# -----------------------------------------------------------------------------
# Anonyme Geräte-ID & Browser-Standort (GPS)
# -----------------------------------------------------------------------------
def init_anonymous_device_id() -> str:
    """Erzeugt oder liest eine anonyme Geräte-ID aus dem Browser-localStorage."""
    if "device_id" not in st.session_state:
        st.session_state.device_id = f"anon_{uuid.uuid4().hex[:16]}"

    query_did = st.query_params.get("did")
    if query_did and isinstance(query_did, str) and query_did.startswith("anon_"):
        st.session_state.device_id = query_did

    fallback_id = st.session_state.device_id
    components.html(
        f"""
        <script>
        (function() {{
            try {{
                const key = "inclusive_bs_device_id";
                let did = window.localStorage.getItem(key);
                if (!did) {{
                    did = "{fallback_id}";
                    window.localStorage.setItem(key, did);
                }}
                const url = new URL(window.parent.location.href);
                if (url.searchParams.get("did") !== did) {{
                    url.searchParams.set("did", did);
                    window.parent.history.replaceState({{}}, "", url.toString());
                }}
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
        width=0
    )
    return st.session_state.device_id


def sync_browser_gps_from_query():
    """Übernimmt vom Browser übergebene GPS-Koordinaten aus den URL-Parametern."""
    ulat = st.query_params.get("ulat")
    ulon = st.query_params.get("ulon")
    if ulat and ulon:
        try:
            lat_val = round(float(ulat), 6)
            lon_val = round(float(ulon), 6)
            if -90 <= lat_val <= 90 and -180 <= lon_val <= 180:
                st.session_state.user_gps = (lat_val, lon_val)
        except ValueError:
            pass


def trigger_browser_gps_request():
    """Fragt einmalig die aktuelle Position über die Browser Geolocation API an."""
    components.html(
        """
        <script>
        (function() {
            if (!navigator.geolocation) {
                alert("Dein Browser unterstützt keine Standortabfrage.");
                return;
            }
            navigator.geolocation.getCurrentPosition(
                function(pos) {
                    const lat = pos.coords.latitude.toFixed(6);
                    const lon = pos.coords.longitude.toFixed(6);
                    const url = new URL(window.parent.location.href);
                    url.searchParams.set("ulat", lat);
                    url.searchParams.set("ulon", lon);
                    window.parent.location.href = url.toString();
                },
                function(err) {
                    alert("Standort konnte nicht abgerufen werden. Bitte erlaube den Standortzugriff im Browser.");
                },
                { enableHighAccuracy: true, timeout: 10000 }
            );
        })();
        </script>
        """,
        height=0,
        width=0
    )


# -----------------------------------------------------------------------------
# Geodaten-, Zeit- & Supabase-Datenbankfunktionen
# -----------------------------------------------------------------------------
def parse_iso_local(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    try:
        dt_utc = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=ZoneInfo("UTC"))
        return dt_utc.astimezone(LOCAL_TZ)
    except Exception:
        return None


def format_local_timestamp(iso_str: str) -> str:
    dt_local = parse_iso_local(iso_str)
    if dt_local:
        return dt_local.strftime("%d.%m.%Y, %H:%M Uhr")
    return iso_str[:16].replace("T", " ")


def format_distance(meters: float) -> str:
    if meters < 1000:
        return f"{int(round(meters / 5.0) * 5)} m"
    return f"{meters / 1000.0:.1f} km"


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def load_data() -> list:
    """Lädt alle gespeicherten Punkte aus der Supabase-Datenbank."""
    url = f"{SUPABASE_URL}/rest/v1/punkte?select=*&order=id.desc"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            rows = resp.json()
            now_local = datetime.now(LOCAL_TZ)
            result = []
            for r in rows:
                valid_until_dt = parse_iso_local(r.get("gueltig_bis"))
                is_expired = bool(valid_until_dt and valid_until_dt <= now_local)
                result.append({
                    "id": r["id"],
                    "Titel": r["titel"],
                    "Kategorie": r["kategorie"],
                    "Status": r["status"],
                    "Beschreibung": r["beschreibung"],
                    "Breitengrad": r["breitengrad"],
                    "Längengrad": r["laengengrad"],
                    "Erstellt am": format_local_timestamp(r["created_at"]),
                    "bestaetigungen": r.get("bestaetigungen", 1),
                    "bewertungen": r.get("bewertungen") or [5],
                    "tags": r.get("tags") or [],
                    "offiziell": r.get("offiziell", False),
                    "voter_ids": r.get("voter_ids") or [],
                    "gueltig_bis_dt": valid_until_dt,
                    "gueltig_bis_str": valid_until_dt.strftime("%d.%m.%Y, %H:%M Uhr") if valid_until_dt else None,
                    "is_expired": is_expired
                })
            return result
    except Exception:
        pass
    return []


def insert_point(punkt: dict) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/punkte"
    payload = {
        "titel": punkt["Titel"],
        "kategorie": punkt["Kategorie"],
        "status": punkt["Status"],
        "beschreibung": punkt["Beschreibung"],
        "breitengrad": punkt["Breitengrad"],
        "laengengrad": punkt["Längengrad"],
        "bestaetigungen": punkt["bestaetigungen"],
        "bewertungen": punkt["bewertungen"],
        "tags": punkt["tags"],
        "offiziell": punkt["offiziell"],
        "voter_ids": punkt["voter_ids"],
        "gueltig_bis": punkt.get("gueltig_bis")
    }
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=10)
    return resp.status_code in (200, 201)


def update_point(point_id: int, updates: dict) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/punkte?id=eq.{point_id}"
    resp = requests.patch(url, headers=HEADERS, json=updates, timeout=10)
    return resp.status_code in (200, 204)


def delete_point(point_id: int) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/punkte?id=eq.{point_id}"
    resp = requests.delete(url, headers=HEADERS, timeout=10)
    return resp.status_code in (200, 204)


def get_avg_rating(punkt: dict) -> float:
    ratings = punkt.get("bewertungen", [])
    return round(sum(ratings) / len(ratings), 1) if ratings else 0.0


def format_stars(avg: float) -> str:
    full = int(round(avg))
    return "★" * full + "☆" * (5 - full) + f" ({avg:.1f})"


def get_marker_color(kategorie: str, status: str, is_verified: bool) -> str:
    if not is_verified:
        return "lightgray"
    if status == "Behoben / Barrierefrei" and kategorie != "Temporärer Ruhe-/Arbeitsraum":
        return "green"
    if status == "In Prüfung":
        return "cadetblue"
    return KATEGORIE_STYLES.get(kategorie, {}).get("color", "blue")


# -----------------------------------------------------------------------------
# Session State & Geräte-ID Initialisierung
# -----------------------------------------------------------------------------
device_id = init_anonymous_device_id()

if "user_gps" not in st.session_state:
    st.session_state.user_gps = None

if "request_gps_now" not in st.session_state:
    st.session_state.request_gps_now = False

sync_browser_gps_from_query()

if "selected_lat" not in st.session_state:
    st.session_state.selected_lat = BS_CENTER_LAT

if "selected_lon" not in st.session_state:
    st.session_state.selected_lon = BS_CENTER_LON

if "selected_location_label" not in st.session_state:
    st.session_state.selected_location_label = "Campus-Zentrum (TU Braunschweig)"

if "map_center" not in st.session_state:
    st.session_state.map_center = [BS_CENTER_LAT, BS_CENTER_LON]

if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = DEFAULT_ZOOM

if "last_preset" not in st.session_state:
    st.session_state.last_preset = "Per Klick auf Karte wählen"

if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

if "min_confirmations" not in st.session_state:
    st.session_state.min_confirmations = DEFAULT_REQUIRED_CONFIRMATIONS

if "last_submit_ts" not in st.session_state:
    st.session_state.last_submit_ts = 0.0

if "feedback_banner" not in st.session_state:
    st.session_state.feedback_banner = None

all_points_raw = load_data()
# Abgelaufene zeitlich begrenzte Einträge für die öffentliche Ansicht ausblenden
active_points = [p for p in all_points_raw if not p.get("is_expired")]
expired_points = [p for p in all_points_raw if p.get("is_expired")]

min_confirmations = st.session_state.min_confirmations if st.session_state.is_admin else DEFAULT_REQUIRED_CONFIRMATIONS

all_verified = [p for p in active_points if p.get("bestaetigungen", 1) >= min_confirmations]
all_unverified = [p for p in active_points if p.get("bestaetigungen", 1) < min_confirmations]
active_issues = [
    p for p in all_verified
    if p["Kategorie"] in ("Aufzug defekt / außer Betrieb", "Sonstiges Hindernis")
    and p["Status"] != "Behoben / Barrierefrei"
]
overall_avg = (
    round(sum(get_avg_rating(p) for p in all_verified) / len(all_verified), 1)
    if all_verified else 0.0
)

# -----------------------------------------------------------------------------
# UI: Kompakter Header & Kennzahlen-Leiste
# -----------------------------------------------------------------------------
header_left, header_right = st.columns([4, 1])
with header_left:
    st.title("♿ Inclusive BS")
    st.caption("Barrierefreiheits-Guide & Campus-Meldeplattform für die TU Braunschweig")
with header_right:
    if st.session_state.is_admin:
        st.success("🛡️ Admin aktiv")

if st.session_state.feedback_banner:
    st.success(st.session_state.feedback_banner)
    st.session_state.feedback_banner = None

m1, m2, m3, m4 = st.columns(4)
m1.metric("✅ Verifizierte Orte", len(all_verified))
m2.metric("⏳ Offene Meldungen", len(all_unverified))
m3.metric("⚠️ Aktive Störungen", len(active_issues))
m4.metric("⭐ Ø Campus-Bewertung", f"{overall_avg:.1f} / 5" if all_verified else "–")

# -----------------------------------------------------------------------------
# UI: Haupt-Navigation über 4 aufgeräumte Tabs
# -----------------------------------------------------------------------------
tab_map, tab_report, tab_queue, tab_about = st.tabs([
    "🗺️ Campus-Karte & Orte",
    "➕ Ort melden",
    f"⏳ Mithelfen & Bestätigen ({len(all_unverified)})",
    "ℹ️ Über das Projekt & Admin"
])

# =============================================================================
# TAB 1: INTERAKTIVE KARTE & VERIFIZIERTE ORTE
# =============================================================================
with tab_map:
    search_col, gps_col, toggle_col = st.columns([2.6, 1.2, 1.2])
    with search_col:
        search_query = st.text_input(
            "Suche",
            placeholder="🔍 Nach Gebäude, Hörsaal, Rampe oder Stichwort suchen (z. B. Audimax, UB, Aufzug)...",
            label_visibility="collapsed"
        )
    with gps_col:
        if st.session_state.user_gps is None:
            if st.button("📍 In meiner Nähe", use_container_width=True):
                st.session_state.request_gps_now = True
        else:
            if st.button("📍 Standort aktiv (Aus)", use_container_width=True):
                st.session_state.user_gps = None
                if "ulat" in st.query_params:
                    del st.query_params["ulat"]
                if "ulon" in st.query_params:
                    del st.query_params["ulon"]
                st.rerun()
    with toggle_col:
        show_unverified_on_map = st.toggle("Unbestätigte grau", value=False)

    if st.session_state.request_gps_now:
        st.session_state.request_gps_now = False
        trigger_browser_gps_request()

    with st.expander("🎛️ Filter nach Kategorie & Status anpassen", expanded=False):
        f_col1, f_col2, f_col3 = st.columns([2, 2, 1])
        with f_col1:
            selected_kategorien = st.multiselect(
                "Kategorien:",
                options=KATEGORIEN,
                default=KATEGORIEN
            )
        with f_col2:
            selected_status = st.multiselect(
                "Status:",
                options=STATUS_OPTIONEN,
                default=STATUS_OPTIONEN
            )
        with f_col3:
            st.write("")
            show_tu_pins = st.checkbox("🏛️ TU-Gebäude", value=True)

    query_lower = search_query.strip().lower()
    matching_points = [
        p for p in active_points
        if p["Kategorie"] in selected_kategorien
        and p["Status"] in selected_status
        and (
            not query_lower
            or query_lower in p["Titel"].lower()
            or query_lower in p["Beschreibung"].lower()
            or query_lower in p["Kategorie"].lower()
            or any(query_lower in t.lower() for t in p.get("tags", []))
        )
    ]

    # Entfernung berechnen & bei aktivem Standort nach Nähe sortieren
    if st.session_state.user_gps:
        u_lat, u_lon = st.session_state.user_gps
        for p in matching_points:
            p["distance_m"] = haversine_meters(u_lat, u_lon, p["Breitengrad"], p["Längengrad"])
        matching_points.sort(key=lambda x: x["distance_m"])
    else:
        for p in matching_points:
            p["distance_m"] = None

    verified_points = [p for p in matching_points if p.get("bestaetigungen", 1) >= min_confirmations]
    unverified_points = [p for p in matching_points if p.get("bestaetigungen", 1) < min_confirmations]
    points_for_map = verified_points + (unverified_points if show_unverified_on_map else [])

    # Karte rendern (bei aktivem GPS auf Nutzerposition zentrieren)
    current_center = list(st.session_state.user_gps) if st.session_state.user_gps else st.session_state.map_center
    m = folium.Map(
        location=current_center,
        zoom_start=st.session_state.map_zoom,
        tiles="OpenStreetMap"
    )

    if st.session_state.user_gps:
        folium.CircleMarker(
            location=list(st.session_state.user_gps),
            radius=9,
            color="#0055FF",
            fill=True,
            fill_color="#3388FF",
            fill_opacity=0.9,
            tooltip="📍 Dein aktueller Standort"
        ).add_to(m)

    if show_tu_pins:
        tu_group = folium.FeatureGroup(name="TU Braunschweig Gebäude")
        for b in TU_CAMPUS_PINS:
            folium.Marker(
                location=[b["lat"], b["lon"]],
                popup=f"<b>{b['name']}</b><br>{b['adresse']}",
                tooltip=f"🏛️ {b['name']}",
                icon=folium.Icon(color="darkred", icon="university", prefix="fa")
            ).add_to(tu_group)
        tu_group.add_to(m)

    for punkt in points_for_map:
        is_verified = punkt.get("bestaetigungen", 1) >= min_confirmations
        marker_color = get_marker_color(punkt["Kategorie"], punkt["Status"], is_verified)
        icon_name = KATEGORIE_STYLES.get(punkt["Kategorie"], {}).get("icon", "info-sign")

        avg_rating = get_avg_rating(punkt)
        stars_str = format_stars(avg_rating)
        tags_html = ", ".join(punkt.get("tags", [])) or "Keine Tags"
        badge = (
            "<span style='color:green; font-weight:bold;'>✔ Verifiziert</span>"
            if is_verified
            else f"<span style='color:gray;'>⏳ Unbestätigt ({punkt['bestaetigungen']}/{min_confirmations})</span>"
        )
        if punkt.get("offiziell"):
            badge += " | <span style='color:#BE1E3C; font-weight:bold;'>🛡️ Offiziell</span>"

        valid_html = (
            f"<p style='margin: 2px 0; color: #0F766E;'><b>🕒 Gültig bis:</b> {punkt['gueltig_bis_str']}</p>"
            if punkt.get("gueltig_bis_str") else ""
        )
        dist_html = (
            f"<p style='margin: 2px 0;'><b>📍 Entfernung:</b> ca. {format_distance(punkt['distance_m'])}</p>"
            if punkt.get("distance_m") is not None else ""
        )

        popup_html = f"""
        <div style="font-family: sans-serif; min-width: 230px;">
            <h4 style="margin: 0 0 4px 0;">{punkt['Titel']}</h4>
            <div style="margin-bottom: 6px; font-size: 12px;">{badge}</div>
            {valid_html}
            {dist_html}
            <p style="margin: 2px 0;"><b>Bewertung:</b> {stars_str} ({len(punkt.get('bewertungen', []))}x)</p>
            <p style="margin: 2px 0;"><b>Kategorie:</b> {punkt['Kategorie']}</p>
            <p style="margin: 2px 0;"><b>Status:</b> {punkt['Status']}</p>
            <p style="margin: 2px 0;"><b>Tags:</b> {tags_html}</p>
            <p style="margin: 6px 0 0 0;">{punkt['Beschreibung']}</p>
            <hr style="margin: 6px 0;">
            <small style="color: gray;">Bestätigungen: {punkt['bestaetigungen']} | Erfasst: {punkt['Erstellt am']}</small>
        </div>
        """

        folium.Marker(
            location=[punkt["Breitengrad"], punkt["Längengrad"]],
            popup=folium.Popup(popup_html, max_width=320),
            tooltip=f"{punkt['Titel']} | {stars_str}",
            icon=folium.Icon(color=marker_color, icon=icon_name, prefix="fa")
        ).add_to(m)

    folium.LayerControl().add_to(m)

    st_folium(
        m,
        width="100%",
        height=480,
        key="inclusive_bs_main_map",
        returned_objects=[]
    )

    st.caption(
        "**Legende:** 🟢 Barrierefrei / Behoben · 🌲 Temporärer Arbeitsraum · 🔴 Aufzug defekt · "
        "🔵 Auditive/Visuelle Hilfe · 🟣 Ruheort · 🟠 Hindernis · 🏛️ TU-Gebäude"
    )

    list_header = (
        f"📍 Verifizierte Campus-Orte ({len(verified_points)}) – sortiert nach Entfernung"
        if st.session_state.user_gps
        else f"📍 Verifizierte Campus-Orte ({len(verified_points)})"
    )
    st.subheader(list_header)

    if verified_points:
        for p in verified_points:
            with st.container(border=True):
                top_l, top_r = st.columns([3.5, 1.5])
                with top_l:
                    title_suffix = " · 🛡️ **Offiziell**" if p.get("offiziell") else ""
                    dist_badge = f" · 📍 **{format_distance(p['distance_m'])}**" if p.get("distance_m") is not None else ""
                    st.markdown(f"#### {p['Titel']}{title_suffix}{dist_badge}")
                    kat_label = KATEGORIE_ICONS_TEXT.get(p["Kategorie"], p["Kategorie"])
                    st.caption(
                        f"{kat_label} · **Status:** {p['Status']} · "
                        f"**Bewertung:** {format_stars(get_avg_rating(p))} ({p['bestaetigungen']} Bestätigungen)"
                    )
                with top_r:
                    if p.get("gueltig_bis_str"):
                        st.info(f"🕒 Frei bis {p['gueltig_bis_str']}")
                    else:
                        st.caption(f"🕒 Erfasst: {p['Erstellt am']}")
                    if p.get("tags"):
                        st.caption("🏷️ " + ", ".join(p["tags"]))

                st.write(p["Beschreibung"])
    else:
        st.info(
            "Keine passenden verifizierten Punkte gefunden. "
            "Passe deine Suche/Filter an oder hilf im Tab **„⏳ Mithelfen & Bestätigen“**, offene Meldungen freizuschalten!"
        )

# =============================================================================
# TAB 2: NEUES HINDERNIS ODER ZUGANG MELDEN
# =============================================================================
with tab_report:
    st.subheader("📍 Neuen Zugang, Ruheort oder Barriere melden")
    st.markdown(
        "Wähle rechts ein **TU-Gebäude** aus oder klicke direkt auf die exakte Stelle auf der Karte – "
        "fülle anschließend links das kurze Formular aus."
    )

    form_col, picker_col = st.columns([1.2, 1])

    with picker_col:
        preset_options = ["Per Klick auf Karte wählen"] + [b["name"] for b in TU_CAMPUS_PINS]
        selected_preset = st.selectbox("🏛️ Gebäude-Schnellauswahl:", options=preset_options)

        if selected_preset != st.session_state.last_preset:
            st.session_state.last_preset = selected_preset
            if selected_preset != "Per Klick auf Karte wählen":
                chosen_building = next(b for b in TU_CAMPUS_PINS if b["name"] == selected_preset)
                st.session_state.selected_lat = chosen_building["lat"]
                st.session_state.selected_lon = chosen_building["lon"]
                st.session_state.selected_location_label = f"{chosen_building['name']} ({chosen_building['adresse']})"
                st.session_state.map_center = [chosen_building["lat"], chosen_building["lon"]]

        st.info(f"📌 **Ausgewählter Standort:** {st.session_state.selected_location_label}")

        picker_map = folium.Map(
            location=st.session_state.map_center,
            zoom_start=17,
            tiles="OpenStreetMap"
        )
        for b in TU_CAMPUS_PINS:
            folium.Marker(
                location=[b["lat"], b["lon"]],
                tooltip=f"🏛️ {b['name']}",
                icon=folium.Icon(color="darkred", icon="university", prefix="fa")
            ).add_to(picker_map)

        folium.Marker(
            location=[st.session_state.selected_lat, st.session_state.selected_lon],
            tooltip="Gewählter Standort für deine Meldung",
            icon=folium.Icon(color="blue", icon="map-pin", prefix="fa")
        ).add_to(picker_map)

        picker_data = st_folium(
            picker_map,
            width="100%",
            height=360,
            key="inclusive_bs_picker_map",
            returned_objects=["last_clicked"]
        )

        if picker_data and picker_data.get("last_clicked"):
            clicked_lat = round(picker_data["last_clicked"]["lat"], 6)
            clicked_lon = round(picker_data["last_clicked"]["lng"], 6)
            if (
                clicked_lat != round(st.session_state.selected_lat, 6)
                or clicked_lon != round(st.session_state.selected_lon, 6)
            ):
                st.session_state.selected_lat = clicked_lat
                st.session_state.selected_lon = clicked_lon
                st.session_state.selected_location_label = f"Kartenpunkt ({clicked_lat:.5f}, {clicked_lon:.5f})"
                st.session_state.map_center = [clicked_lat, clicked_lon]
                st.rerun()

    with form_col:
        with st.form("add_point_form", clear_on_submit=True):
            titel = st.text_input(
                "Titel / genauer Ort *",
                placeholder='z. B. "Hörsaal SN 19.1 – Frei bis 16 Uhr" oder "Audimax – Rampe"'
            )
            kategorie = st.selectbox(
                "Kategorie *",
                options=KATEGORIEN,
                format_func=lambda k: KATEGORIE_ICONS_TEXT.get(k, k)
            )
            status = st.selectbox("Aktueller Status *", options=STATUS_OPTIONEN)

            # Zeitliche Begrenzung (ideal für kurzfristig freie Räume oder Baustellen)
            is_time_limited = st.checkbox(
                "🕒 Zeitlich begrenzt (z. B. Raum nur heute bis zu einer bestimmten Uhrzeit frei)",
                value=False
            )
            now_de = datetime.now(LOCAL_TZ)
            default_end = now_de + timedelta(hours=2)
            t_col1, t_col2 = st.columns(2)
            with t_col1:
                valid_date = st.date_input("Gültig bis (Datum):", value=default_end.date())
            with t_col2:
                valid_time = st.time_input("Gültig bis (Uhrzeit):", value=default_end.time().replace(second=0, microsecond=0))

            bewertung = st.slider(
                "Qualitäts-Bewertung (1 = unzugänglich / schlecht, 5 = vorbildlich)",
                min_value=1,
                max_value=5,
                value=4
            )
            ausgewaehlte_tags = st.multiselect(
                "Eigenschaften / Tags (optional):",
                options=BEWERTUNGS_TAGS
            )
            beschreibung = st.text_area(
                "Detailbeschreibung *",
                placeholder="Beschreibe die Situation vor Ort (z. B. Etage, Raumnummer, Eingang, Türbreite)...",
                height=105
            )

            lat = float(st.session_state.selected_lat)
            lon = float(st.session_state.selected_lon)

            if st.session_state.is_admin:
                offizieller_partner = st.checkbox(
                    "🛡️ Als offizieller Partner melden (sofort auf der Karte sichtbar)",
                    value=True
                )
            else:
                offizieller_partner = False

            enable_clustering = st.checkbox(
                "🔗 Nahegelegene Meldungen (< 10m) automatisch zusammenfassen",
                value=True
            )

            submitted = st.form_submit_button("✅ Meldung absenden", type="primary", use_container_width=True)

            if submitted:
                now = time.time()
                seconds_since_last = now - st.session_state.last_submit_ts

                gueltig_bis_iso = None
                valid_dt_local = None
                if is_time_limited:
                    valid_dt_local = datetime.combine(valid_date, valid_time).replace(tzinfo=LOCAL_TZ)
                    gueltig_bis_iso = valid_dt_local.isoformat()

                if not titel.strip():
                    st.error("Bitte gib einen Titel oder Ort an.")
                elif not beschreibung.strip():
                    st.error("Bitte füge eine kurze Detailbeschreibung hinzu.")
                elif is_time_limited and valid_dt_local <= datetime.now(LOCAL_TZ):
                    st.error("Bitte wähle für zeitlich begrenzte Einträge einen Zeitpunkt in der Zukunft.")
                elif not st.session_state.is_admin and seconds_since_last < SUBMIT_COOLDOWN_SECONDS:
                    wait_sec = int(SUBMIT_COOLDOWN_SECONDS - seconds_since_last)
                    st.warning(f"Bitte warte noch {wait_sec} Sekunden, bevor du eine weitere Meldung absendest.")
                else:
                    existing_match = None
                    if enable_clustering and not is_time_limited:
                        for p in active_points:
                            if p.get("offiziell") and not st.session_state.is_admin:
                                continue
                            if p.get("gueltig_bis_str"):
                                continue
                            dist = haversine_meters(lat, lon, p["Breitengrad"], p["Längengrad"])
                            if dist <= CLUSTER_RADIUS_METERS and p["Kategorie"] == kategorie:
                                existing_match = p
                                break

                    punkte_zuwachs = DEFAULT_REQUIRED_CONFIRMATIONS if offizieller_partner else 1

                    if existing_match:
                        existing_voters = existing_match.get("voter_ids", [])
                        if not st.session_state.is_admin and device_id in existing_voters:
                            st.warning(
                                f"Du hast den bestehenden Punkt **„{existing_match['Titel']}“** an dieser Stelle bereits gemeldet oder bestätigt."
                            )
                        else:
                            new_ratings = existing_match["bewertungen"] + [bewertung]
                            new_tags = list(dict.fromkeys(existing_match["tags"] + ausgewaehlte_tags))
                            new_desc = existing_match["Beschreibung"]
                            if beschreibung.strip() not in new_desc:
                                new_desc += f" | Ergänzung: {beschreibung.strip()}"
                            updated_voters = list(dict.fromkeys(existing_voters + [device_id]))
                            new_total = existing_match["bestaetigungen"] + punkte_zuwachs

                            if update_point(existing_match["id"], {
                                "bestaetigungen": new_total,
                                "bewertungen": new_ratings,
                                "tags": new_tags,
                                "beschreibung": new_desc,
                                "offiziell": existing_match["offiziell"] or offizieller_partner,
                                "voter_ids": updated_voters
                            }):
                                st.session_state.last_submit_ts = now
                                st.session_state.feedback_banner = (
                                    f"✅ Deine Ergänzung wurde mit „{existing_match['Titel']}“ "
                                    f"zusammengeführt ({new_total}/{DEFAULT_REQUIRED_CONFIRMATIONS} Bestätigungen)."
                                )
                                st.rerun()
                            else:
                                st.error("Fehler beim Aktualisieren in der Cloud-Datenbank.")
                    else:
                        neuer_punkt = {
                            "Titel": titel.strip(),
                            "Kategorie": kategorie,
                            "Status": status,
                            "Beschreibung": beschreibung.strip(),
                            "Breitengrad": round(lat, 6),
                            "Längengrad": round(lon, 6),
                            "bestaetigungen": punkte_zuwachs,
                            "bewertungen": [bewertung],
                            "tags": ausgewaehlte_tags,
                            "offiziell": offizieller_partner,
                            "voter_ids": [device_id],
                            "gueltig_bis": gueltig_bis_iso
                        }
                        if insert_point(neuer_punkt):
                            st.session_state.last_submit_ts = now
                            if offizieller_partner:
                                st.session_state.feedback_banner = (
                                    f"🛡️ Offizieller Eintrag „{titel.strip()}“ wurde gespeichert und ist sofort auf der Karte sichtbar!"
                                )
                            else:
                                fehlend = DEFAULT_REQUIRED_CONFIRMATIONS - 1
                                st.session_state.feedback_banner = (
                                    f"✅ Meldung „{titel.strip()}“ gespeichert! Sie steht jetzt im Tab „⏳ Mithelfen & Bestätigen“ "
                                    f"und wird nach {fehlend} weiteren Bestätigungen auf der Karte freigeschaltet."
                                )
                            st.rerun()
                        else:
                            st.error("Fehler beim Speichern in der Cloud-Datenbank.")

# =============================================================================
# TAB 3: WARTESCHLANGE – MITHELFEN & BESTÄTIGEN
# =============================================================================
with tab_queue:
    st.subheader("⏳ Neue Meldungen prüfen & bestätigen")
    st.markdown(
        f"Um Fake-Einträge zu vermeiden, erscheinen neue Meldungen erst ab **{min_confirmations} Bestätigungen** "
        "auf der öffentlichen Campus-Karte. Warst du vor Ort? Bestätige den Punkt mit einem Klick!"
    )

    if all_unverified:
        for p in all_unverified:
            already_voted = (not st.session_state.is_admin) and (device_id in p.get("voter_ids", []))
            with st.container(border=True):
                c1, c2, c3 = st.columns([3.2, 1.4, 1.4])
                with c1:
                    kat_label = KATEGORIE_ICONS_TEXT.get(p["Kategorie"], p["Kategorie"])
                    st.markdown(f"#### {p['Titel']}")
                    st.caption(
                        f"{kat_label} · Fortschritt: **{p['bestaetigungen']} / {min_confirmations} Bestätigungen** · "
                        f"Bewertung: {format_stars(get_avg_rating(p))} · Erfasst: {p['Erstellt am']}"
                    )
                    if p.get("gueltig_bis_str"):
                        st.caption(f"🕒 Zeitlich begrenzt bis: **{p['gueltig_bis_str']}**")
                    st.write(p["Beschreibung"])
                    if p.get("tags"):
                        st.caption("🏷️ " + ", ".join(p["tags"]))
                with c2:
                    if already_voted:
                        st.write("")
                        st.info("✔ Von deinem Gerät bestätigt")
                    else:
                        sterne_confirm = st.selectbox(
                            "Deine Bewertung:",
                            options=[5, 4, 3, 2, 1],
                            format_func=lambda x: f"{x} ★",
                            key=f"stars_{p['id']}"
                        )
                        if st.button("👍 Vor Ort bestätigen", key=f"confirm_{p['id']}", type="primary", use_container_width=True):
                            updated_voters = list(dict.fromkeys(p.get("voter_ids", []) + [device_id]))
                            update_point(p["id"], {
                                "bestaetigungen": p["bestaetigungen"] + 1,
                                "bewertungen": p["bewertungen"] + [sterne_confirm],
                                "voter_ids": updated_voters
                            })
                            st.rerun()
                with c3:
                    st.write("")
                    st.write("")
                    if not already_voted:
                        if st.button("🚫 Falschmeldung", key=f"spam_{p['id']}", use_container_width=True):
                            updated_voters = list(dict.fromkeys(p.get("voter_ids", []) + [device_id]))
                            if p["bestaetigungen"] - 1 <= 0:
                                delete_point(p["id"])
                            else:
                                update_point(p["id"], {
                                    "bestaetigungen": p["bestaetigungen"] - 1,
                                    "voter_ids": updated_voters
                                })
                            st.rerun()
    else:
        st.success("🎉 Aktuell sind alle Meldungen geprüft – keine offenen Punkte in der Warteschlange!")

# =============================================================================
# TAB 4: ÜBER DAS PROJEKT & ADMIN-BEREICH
# =============================================================================
with tab_about:
    st.subheader("ℹ️ Über Inclusive BS")
    st.markdown(
        """
        **Inclusive BS** ist eine studentische Initiative für mehr Transparenz zur Barrierefreiheit rund um den Campus der **TU Braunschweig**.
        Ziel ist es, sowohl dauerhafte Zugänge (Rampen, Aufzüge, ruhige Lernorte) als auch akute Hindernisse (defekte Aufzüge, Baustellen) in Echtzeit sichtbar zu machen.

        ### 🔒 Wie funktioniert die Qualitätssicherung & der Datenschutz?
- **Kein Account & keine persönlichen Daten nötig:** Du musst weder Namen noch E-Mail-Adresse angeben. Dein Browser speichert lediglich eine zufällige, anonyme Kennung (`localStorage`), damit jeder Punkt pro Gerät nur einmal bestätigt werden kann.
- **3-Augen-Prinzip:** Neue Meldungen starten in der Warteschlange und werden automatisch auf der Hauptkarte sichtbar, sobald **3 verschiedene Geräte** sie vor Ort bestätigt haben.
- **Offizielle Campus-Partner:** Verifizierte Stellen (z. B. AStA, Schwerbehindertenvertretung oder Projekt-Moderation) können gesicherte Hinweise direkt freischalten.
        """
    )

    st.divider()
    st.subheader("🔐 Admin- & Moderationsbereich")

    if not st.session_state.is_admin:
        with st.form("admin_login_form"):
            pw_col, btn_col = st.columns([3, 1])
            with pw_col:
                pw_input = st.text_input(
                    "Admin-Passwort",
                    type="password",
                    placeholder="Passwort für TU-Moderation eingeben...",
                    label_visibility="collapsed"
                )
            with btn_col:
                login_submitted = st.form_submit_button("Anmelden", use_container_width=True)

            if login_submitted:
                if ADMIN_PASSWORD and pw_input == ADMIN_PASSWORD:
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("Falsches Passwort oder kein Admin-Passwort konfiguriert.")
    else:
        st.success("🛡️ Admin-Modus ist aktiv – du kannst jetzt offizielle Punkte erstellen sowie Einträge bearbeiten oder löschen.")

        adm_c1, adm_c2 = st.columns([2, 1])
        with adm_c1:
            new_min_conf = st.slider(
                "Benötigte Bestätigungen für Hauptkarte (Standard: 3):",
                min_value=3,
                max_value=30,
                value=st.session_state.min_confirmations
            )
            if new_min_conf != st.session_state.min_confirmations:
                st.session_state.min_confirmations = new_min_conf
                st.rerun()
        with adm_c2:
            st.write("")
            if st.button("🚪 Admin-Modus beenden", use_container_width=True):
                st.session_state.is_admin = False
                st.rerun()

        if all_points_raw:
            st.markdown("#### 🛠️ Gespeicherte Einträge verwalten")
            admin_options = {
                f"#{p['id']} · {p['Titel']} ({p['Status']} | {p['bestaetigungen']} Bestätigungen"
                + (" | ⌛ Abgelaufen)" if p.get("is_expired") else ")"): p
                for p in all_points_raw
            }
            selected_label = st.selectbox("Eintrag auswählen:", options=list(admin_options.keys()))
            target_point = admin_options[selected_label]

            valid_info = f" · Gültig bis: {target_point['gueltig_bis_str']}" if target_point.get("gueltig_bis_str") else ""
            st.caption(
                f"Koordinaten: `{target_point['Breitengrad']}, {target_point['Längengrad']}` · "
                f"Offiziell: {'Ja' if target_point.get('offiziell') else 'Nein'}{valid_info}"
            )

            col_a1, col_a2, col_a3 = st.columns([2, 1.5, 1.5])
            with col_a1:
                new_status = st.selectbox(
                    "Status ändern:",
                    options=STATUS_OPTIONEN,
                    index=STATUS_OPTIONEN.index(target_point["Status"]) if target_point["Status"] in STATUS_OPTIONEN else 0
                )
            with col_a2:
                st.write("")
                st.write("")
                if st.button("💾 Status speichern", use_container_width=True):
                    update_point(target_point["id"], {"status": new_status})
                    st.rerun()
            with col_a3:
                st.write("")
                st.write("")
                if st.button("🗑️ Punkt endgültig löschen", type="primary", use_container_width=True):
                    delete_point(target_point["id"])
                    st.rerun()
