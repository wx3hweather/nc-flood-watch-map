#!/usr/bin/env python3
"""
nc_flood_watch_map.py

Fetches currently active NWS Flood Watch alerts for North Carolina and
renders a static PNG map showing which areas are affected.

Data source: api.weather.gov (NWS's public alerts API, no key required).
Designed to be run on a schedule (e.g., via cron) to keep the map current.

Usage:
    python3 nc_flood_watch_map.py

Requires: requests, geopandas, shapely, matplotlib
    pip install requests geopandas shapely matplotlib
"""

from __future__ import annotations

import io
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")  # safe for headless/cron runs
import matplotlib.pyplot as plt
import requests
from shapely.geometry import shape

# ---------- Configuration ----------
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"
STATE_BOUNDARY_URL = (
    "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/"
    "master/data/geojson/us-states.json"
)
STATE_ABBR = "NC"
STATE_NAME = "North Carolina"
EVENT_TYPE = "Flood Watch"
OUTPUT_PATH = Path(__file__).with_name("nc_flood_watch_map.png")

# NWS asks that requests include a descriptive User-Agent with contact info.
# Please replace the email below with your own.
USER_AGENT = "nc-flood-watch-map (contact: your_email@example.com)"
REQUEST_TIMEOUT = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def fetch_active_alerts(state_abbr: str, event_type: str) -> list[dict]:
    """Query api.weather.gov for currently active alerts of a given type in a state."""
    params = {"area": state_abbr, "event": event_type}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    resp = requests.get(NWS_ALERTS_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("features", [])


def fetch_zone_geometry(zone_url: str) -> dict | None:
    """
    Many Flood Watch alerts don't carry an explicit polygon and instead
    reference UGC zones (e.g. https://api.weather.gov/zones/county/NCC183).
    Fetch that zone's geometry as a fallback.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    try:
        resp = requests.get(zone_url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("geometry")
    except requests.RequestException as exc:
        log.warning("Could not fetch zone geometry for %s: %s", zone_url, exc)
        return None


def alerts_to_geodataframe(alerts: list[dict]) -> gpd.GeoDataFrame:
    """Convert NWS alert features into a GeoDataFrame of polygons, resolving
    zone-only alerts by fetching their zone geometry."""
    columns = ["event", "headline", "effective", "expires", "area_desc", "geometry"]
    records = []

    for feature in alerts:
        props = feature.get("properties", {})
        geom = feature.get("geometry")

        geometries_to_use = []
        if geom:
            geometries_to_use.append(shape(geom))
        else:
            for zone_url in props.get("affectedZones", []):
                zgeom = fetch_zone_geometry(zone_url)
                if zgeom:
                    geometries_to_use.append(shape(zgeom))

        for g in geometries_to_use:
            records.append(
                {
                    "event": props.get("event"),
                    "headline": props.get("headline"),
                    "effective": props.get("effective"),
                    "expires": props.get("expires"),
                    "area_desc": props.get("areaDesc"),
                    "geometry": g,
                }
            )

    if not records:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs="EPSG:4326")

    return gpd.GeoDataFrame(records, columns=columns, geometry="geometry", crs="EPSG:4326")


def load_state_boundary(state_name: str) -> gpd.GeoDataFrame:
    """Fetch a US states GeoJSON and return just the requested state.
    Uses `requests` first (rather than letting geopandas open the URL
    directly) since some network setups mishandle direct remote reads."""
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(STATE_BOUNDARY_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    states = gpd.read_file(io.BytesIO(resp.content))
    match = states[states["name"] == state_name]
    if match.empty:
        raise RuntimeError(f"Could not find {state_name!r} in state boundary file")
    return match


def make_map(state_boundary: gpd.GeoDataFrame, watches: gpd.GeoDataFrame, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 7))

    state_boundary.plot(ax=ax, color="#f0f0f0", zorder=0)
    state_boundary.boundary.plot(ax=ax, color="black", linewidth=1, zorder=1)

    if not watches.empty:
        watches.plot(ax=ax, color="#2b7bba", alpha=0.55, edgecolor="#08306b", linewidth=0.8, zorder=2)

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ax.set_title(f"Active NWS Flood Watches — North Carolina\nGenerated {generated}", fontsize=13)
    ax.set_axis_off()

    if watches.empty:
        caption = "No active flood watches"
    else:
        caption = f"{len(watches)} area(s)/zone(s) currently under a flood watch"
    ax.text(0.5, 0.02, caption, transform=ax.transAxes, ha="center", fontsize=10, color="gray")

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    log.info("Map saved to %s", output_path.resolve())


def main() -> None:
    log.info("Fetching active %s alerts for %s...", EVENT_TYPE, STATE_ABBR)
    alerts = fetch_active_alerts(STATE_ABBR, EVENT_TYPE)
    log.info("Found %d matching alert(s)", len(alerts))

    watches_gdf = alerts_to_geodataframe(alerts)
    state_boundary = load_state_boundary(STATE_NAME)

    make_map(state_boundary, watches_gdf, OUTPUT_PATH)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Failed to generate flood watch map")
        sys.exit(1)
