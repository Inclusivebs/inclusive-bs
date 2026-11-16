import math
import time
import uuid
from datetime import datetime
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
    page_title="Inclusive BS - Barrierefreiheits-Guide Braunschweig",
    page_icon="♿",
    layout="wide"
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
    "Sonstiges Hindernis"
]

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
    "Gut ausgeschildert"
]

KATEGORIE_STYLES = {
    "Rollstuhlgerecht / Rampe": {"color": "green", "icon": "wheelchair"},
    "Aufzug defekt / außer Betrieb": {"color": "red", "icon": "exclamation-triangle"},
    "Auditive / Visuelle Hilfe": {"color": "blue", "icon": "volume-up"},
    "Ruheort": {"color": "purple", "icon": "leaf"},
    "Sonstiges Hindernis": {"color": "orange", "icon": "info-sign"}
}

TU_CAMPUS_PINS = [
    {"name": "Altgebäude (Hauptgebäude TU BS)", "adresse": "Pockelsstraße 4", "lat": 52.27345, "lon": 10.52765},
    {"name": "Okerhochhaus", "adresse": "Pockelsstraße 14", "lat": 52.27245, "lon": 10.52755},
    {"name": "Universitätsbibliothek (UB)", "adresse": "Universitätsplatz 1", "lat": 52.27430, "lon": 10.52970},
    {"name": "Forum-Gebäude / Hochhaus", "adresse": "Universitätsplatz 2", "lat": 52.27345, "lon": 10.52898},
    {"name": "Auditorium Maximum (Audimax)", "adresse": "Universitätsplatz 3", "lat": 52.27275, "lon": 10.53005},
    {"name": "Biozentrum", "adresse": "Spielmannstraße 7", "lat": 52.27415, "lon": 10.53130},
    {"name": "Architektur-Pavillons", "adresse": "Pockelsstraße 3", "lat": 52.27340, "lon": 10.52625},
    {"name": "Mensa 1", "adresse": "Katharinenstraße 1", "lat": 52.27520, "lon": 10.52790},
    {"name": "Informatikzentrum (IZ)", "adresse": "Mühlenpfordtstraße 23", "lat": 52.27525, "lon": 10.52530},
]

# -----------------------------------------------------------------------------
# Anonyme Geräte-ID über Browser-localStorage (ohne Login / personenbezogene Daten)
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


# -----------------------------------------------------------------------------
# Geodaten-, Zeit- & Supabase-Datenbankfunktionen
# -----------------------------------------------------------------------------
def format_local_timestamp(iso_str: str) -> str:
    """Wandelt einen UTC-ISO-Zeitstempel aus Supabase in deutsche Ortszeit um."""
    try:
        dt_utc = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_local = dt_utc.astimezone(LOCAL_TZ)
        return dt_local.strftime("%d.%m.%Y, %H:%M Uhr")
    except Exception:
        return iso_str[:16].replace("T", " ")


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def load_data() -> list:
    """Lädt alle gespeicherten Punkte aus der Supabase-Datenbank."""
    url = f"{SUPABASE_URL}/rest/v1/punkte?select=*&order=id.asc"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            rows = resp.json()
            return [
                {
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
                    "voter_ids": r.get("voter_ids") or []
                }
                for r in rows
            ]
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
        "voter_ids": punkt["voter_ids"]
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
    if status == "Behoben / Barrierefrei":
        return "green"
    if status == "In Prüfung":
        return "cadetblue"
    return KATEGORIE_STYLES.get(kategorie, {}).get("color", "blue")


# -----------------------------------------------------------------------------
# Session State & Geräte-ID Initialisierung
# -----------------------------------------------------------------------------
device_id = init_anonymous_device_id()

if "selected_lat" not in st.session_state:
    st.session_state.selected_lat = BS_CENTER_LAT

if "selected_lon" not in st.session_state:
    st.session_state.selected_lon = BS_CENTER_LON

if "map_center" not in st.session_state:
    st.session_state.map_center = [BS_CENTER_LAT, BS_CENTER_LON]

if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = DEFAULT_ZOOM

if "last_preset" not in st.session_state:
    st.session_state.last_preset = "Per Klick auf Karte wählen"

if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

if "last_submit_ts" not in st.session_state:
    st.session_state.last_submit_ts = 0.0

if "feedback_banner" not in st.session_state:
    st.session_state.feedback_banner = None

all_points = load_data()

# -----------------------------------------------------------------------------
# UI: Kopfbereich & Feedback-Banner
# -----------------------------------------------------------------------------
st.title("♿ Inclusive BS – Barrierefreiheits-Guide TU Braunschweig")
st.markdown(
    "Melde barrierefreie Zugänge, defekte Aufzüge, Ruheorte oder Hindernisse rund um den Campus. "
    "**Tipp:** Klicke direkt auf eine Stelle in der Karte – die Koordinaten werden automatisch ins Formular übernommen "
    "*(📱 am Smartphone: tippe oben links auf **`>`**, um das Meldeformular zu öffnen)*."
)

if st.session_state.feedback_banner:
    st.success(st.session_state.feedback_banner)
    st.session_state.feedback_banner = None

# -----------------------------------------------------------------------------
# UI: Sidebar – Eingabemaske & Admin-Zugang
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("📍 Neuen Punkt melden")

    preset_options = ["Per Klick auf Karte wählen"] + [b["name"] for b in TU_CAMPUS_PINS]
    selected_preset = st.selectbox("Gebäude-Schnellauswahl:", options=preset_options)

    if selected_preset != st.session_state.last_preset:
        st.session_state.last_preset = selected_preset
        if selected_preset != "Per Klick auf Karte wählen":
            chosen_building = next(b for b in TU_CAMPUS_PINS if b["name"] == selected_preset)
            st.session_state.selected_lat = chosen_building["lat"]
            st.session_state.selected_lon = chosen_building["lon"]
            st.session_state.map_center = [chosen_building["lat"], chosen_building["lon"]]

    st.info(f"📌 Gewählte Position:\n**Lat:** `{st.session_state.selected_lat:.6f}` | **Lon:** `{st.session_state.selected_lon:.6f}`")

    with st.form("add_point_form", clear_on_submit=True):
        titel = st.text_input(
            "Titel / Ort *",
            placeholder='z. B. "Audimax - Rampe Haupteingang"'
        )
        kategorie = st.selectbox("Kategorie *", options=KATEGORIEN)
        status = st.selectbox("Status *", options=STATUS_OPTIONEN)

        bewertung = st.slider(
            "Qualitäts-Bewertung (1 = schlecht, 5 = sehr gut)",
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
            placeholder="Beschreibe die Situation vor Ort (z. B. Etage, Eingang, Türbreite)..."
        )

        coord_key = f"{st.session_state.selected_lat}_{st.session_state.selected_lon}"
        col_lat, col_lon = st.columns(2)
        with col_lat:
            lat = st.number_input(
                "Breitengrad (Lat)",
                value=float(st.session_state.selected_lat),
                format="%.6f",
                step=0.0001,
                key=f"lat_{coord_key}"
            )
        with col_lon:
            lon = st.number_input(
                "Längengrad (Lon)",
                value=float(st.session_state.selected_lon),
                format="%.6f",
                step=0.0001,
                key=f"lon_{coord_key}"
            )

        if st.session_state.is_admin:
            offizieller_partner = st.checkbox(
                "🛡️ Als offizieller Partner melden (sofort auf der Karte sichtbar)",
                value=True
            )
        else:
            offizieller_partner = False

        enable_clustering = st.checkbox(
            "🔗 Nahegelegene Meldungen (< 10m) zusammenfassen",
            value=True
        )

        submitted = st.form_submit_button("✅ Meldung absenden", use_container_width=True)

        if submitted:
            now = time.time()
            seconds_since_last = now - st.session_state.last_submit_ts

            if not titel.strip():
                st.error("Bitte gib einen Titel oder Ort an.")
            elif not beschreibung.strip():
                st.error("Bitte füge eine kurze Detailbeschreibung hinzu.")
            elif not st.session_state.is_admin and seconds_since_last < SUBMIT_COOLDOWN_SECONDS:
                wait_sec = int(SUBMIT_COOLDOWN_SECONDS - seconds_since_last)
                st.warning(f"Bitte warte noch {wait_sec} Sekunden, bevor du eine weitere Meldung absendest.")
            else:
                existing_match = None
                if enable_clustering:
                    for p in all_points:
                        # Offizielle Admin-Pins sind vor Überschreiben durch Gäste geschützt
                        if p.get("offiziell") and not st.session_state.is_admin:
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
                                f"✅ Deine Ergänzung wurde mit dem bestehenden Punkt „{existing_match['Titel']}“ "
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
                        "voter_ids": [device_id]
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
                                f"✅ Meldung „{titel.strip()}“ gespeichert! Sie steht jetzt im Tab „⏳ Warteschlange“ "
                                f"und wird nach {fehlend} weiteren Bestätigungen durch andere Personen auf der Karte freigeschaltet."
                            )
                        st.rerun()
                    else:
                        st.error("Fehler beim Speichern in der Cloud-Datenbank.")

    st.divider()

    # -------------------------------------------------------------------------
    # Admin-Login & Moderation
    # -------------------------------------------------------------------------
    min_confirmations = DEFAULT_REQUIRED_CONFIRMATIONS
    with st.expander("🔐 Admin-Bereich (TU / Moderation)"):
        if not st.session_state.is_admin:
            pw_input = st.text_input("Admin-Passwort:", type="password", key="admin_pw_input")
            if st.button("Anmelden", use_container_width=True):
                if ADMIN_PASSWORD and pw_input == ADMIN_PASSWORD:
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("Falsches Passwort oder kein Admin-Passwort konfiguriert.")
        else:
            st.success("Admin-Modus aktiv")
            min_confirmations = st.slider(
                "Benötigte Bestätigungen für Karte:",
                min_value=1,
                max_value=5,
                value=DEFAULT_REQUIRED_CONFIRMATIONS
            )
            if st.button("Abmelden", use_container_width=True):
                st.session_state.is_admin = False
                st.rerun()

# -----------------------------------------------------------------------------
# UI: Filter-Optionen & Karten-Legende
# -----------------------------------------------------------------------------
st.subheader("🗺️ Interaktive Campus- & Stadtkarte")

filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 1.3])
with filter_col1:
    selected_kategorien = st.multiselect(
        "Nach Kategorie filtern:",
        options=KATEGORIEN,
        default=KATEGORIEN
    )
with filter_col2:
    selected_status = st.multiselect(
        "Nach Status filtern:",
        options=STATUS_OPTIONEN,
        default=STATUS_OPTIONEN
    )
with filter_col3:
    show_tu_pins = st.checkbox("🏛️ TU-Gebäude-Pins", value=True)
    show_unverified_on_map = st.checkbox("👁️ Unbestätigte grau zeigen", value=False)

st.caption(
    "**Legende:** 🟢 Barrierefrei / Behoben · 🔴 Aufzug defekt · 🔵 Auditive/Visuelle Hilfe · "
    "🟣 Ruheort · 🟠 Hindernis · ⏳ In Prüfung (Hellblau) · 🏛️ TU-Gebäude (Dunkelrot)"
)

matching_points = [
    p for p in all_points
    if p["Kategorie"] in selected_kategorien and p["Status"] in selected_status
]

verified_points = [p for p in matching_points if p.get("bestaetigungen", 1) >= min_confirmations]
unverified_points = [p for p in matching_points if p.get("bestaetigungen", 1) < min_confirmations]
points_for_map = verified_points + (unverified_points if show_unverified_on_map else [])

# -----------------------------------------------------------------------------
# Folium Karte erstellen
# -----------------------------------------------------------------------------
m = folium.Map(
    location=st.session_state.map_center,
    zoom_start=st.session_state.map_zoom,
    tiles="OpenStreetMap"
)

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

folium.CircleMarker(
    location=[st.session_state.selected_lat, st.session_state.selected_lon],
    radius=7,
    color="#0066CC",
    fill=True,
    fill_color="#3399FF",
    fill_opacity=0.8,
    tooltip="Aktuell gewählte Position für neue Meldung"
).add_to(m)

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

    popup_html = f"""
    <div style="font-family: sans-serif; min-width: 230px;">
        <h4 style="margin: 0 0 4px 0;">{punkt['Titel']}</h4>
        <div style="margin-bottom: 6px; font-size: 12px;">{badge}</div>
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

map_data = st_folium(
    m,
    width="100%",
    height=540,
    key="inclusive_bs_map",
    returned_objects=["last_clicked", "center", "zoom"]
)

if map_data and map_data.get("last_clicked"):
    clicked_lat = round(map_data["last_clicked"]["lat"], 6)
    clicked_lon = round(map_data["last_clicked"]["lng"], 6)
    if (
        clicked_lat != round(st.session_state.selected_lat, 6)
        or clicked_lon != round(st.session_state.selected_lon, 6)
    ):
        st.session_state.selected_lat = clicked_lat
        st.session_state.selected_lon = clicked_lon
        if map_data.get("center"):
            st.session_state.map_center = [map_data["center"]["lat"], map_data["center"]["lng"]]
        if map_data.get("zoom"):
            st.session_state.map_zoom = map_data["zoom"]
        st.rerun()

# -----------------------------------------------------------------------------
# UI: Tabs unter der Karte
# -----------------------------------------------------------------------------
st.divider()
tab_verified, tab_queue = st.tabs([
    f"✅ Verifizierte Punkte auf der Karte ({len(verified_points)})",
    f"⏳ Warteschlange – Bitte bestätigen ({len(unverified_points)})"
])

with tab_verified:
    if verified_points:
        table_data = [
            {
                "Titel": p["Titel"],
                "Kategorie": p["Kategorie"],
                "Bewertung": format_stars(get_avg_rating(p)),
                "Bestätigungen": f"{p['bestaetigungen']}" + (" 🛡️" if p.get("offiziell") else ""),
                "Status": p["Status"],
                "Koordinaten": f"{p['Breitengrad']}, {p['Längengrad']}",
                "Beschreibung": p["Beschreibung"]
            }
            for p in verified_points
        ]
        st.table(table_data)
    else:
        st.info(
            f"Aktuell gibt es noch keine Punkte mit mindestens {min_confirmations} Bestätigungen. "
            "Wechsle in den Tab 'Warteschlange', um gemeldete Punkte zu bestätigen!"
        )

    # Admin-Moderation (Status ändern / offiziell markieren / löschen)
    if st.session_state.is_admin and all_points:
        with st.expander("🛠️ Admin-Moderation: Einträge bearbeiten oder löschen"):
            admin_options = {f"{p['Titel']} (ID {p['id']} | {p['Status']})": p for p in all_points}
            selected_label = st.selectbox("Eintrag auswählen:", options=list(admin_options.keys()))
            target_point = admin_options[selected_label]

            col_a1, col_a2, col_a3 = st.columns([2, 1.5, 1.5])
            with col_a1:
                new_status = st.selectbox(
                    "Status aktualisieren:",
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
                if st.button("🗑️ Punkt löschen", type="primary", use_container_width=True):
                    delete_point(target_point["id"])
                    st.rerun()

with tab_queue:
    st.caption(
        f"Hier erscheinen neue Meldungen, die noch weniger als **{min_confirmations} Bestätigungen** haben. "
        "Jedes Gerät kann einen Eintrag einmalig bestätigen."
    )
    if unverified_points:
        for p in unverified_points:
            already_voted = (not st.session_state.is_admin) and (device_id in p.get("voter_ids", []))
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 1.5, 1.5])
                with c1:
                    st.markdown(f"**{p['Titel']}** ({p['Kategorie']})")
                    st.caption(
                        f"Fortschritt: **{p['bestaetigungen']} / {min_confirmations} Bestätigungen** | "
                        f"Bewertung: {format_stars(get_avg_rating(p))} | "
                        f"Koordinaten: {p['Breitengrad']}, {p['Längengrad']}"
                    )
                    st.write(p["Beschreibung"])
                with c2:
                    if already_voted:
                        st.write("")
                        st.info("✔ Von deinem Gerät bestätigt")
                    else:
                        sterne_confirm = st.selectbox(
                            "Bewertung:",
                            options=[5, 4, 3, 2, 1],
                            format_func=lambda x: f"{x} ★",
                            key=f"stars_{p['id']}"
                        )
                        if st.button("👍 Vor Ort bestätigen", key=f"confirm_{p['id']}", use_container_width=True):
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
                        if st.button("🚫 Falschmeldung / Spam", key=f"spam_{p['id']}", use_container_width=True):
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
        st.success("Keine offenen unbestätigten Meldungen in der Warteschlange.")
