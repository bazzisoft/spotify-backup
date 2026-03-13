#!/usr/bin/env python3
"""
Spotify Backup Script (v2) — uses the spotipy library.

Backs up liked songs, playlists, saved episodes and followed podcasts to a JSON file.

Usage:
    # First run — authenticate interactively (opens browser, caches token):
    python spotify-backup-v2.py --auth --client-id ID --client-secret SECRET output.json

    # Subsequent automated runs — uses cached refresh token, no browser needed:
    python spotify-backup-v2.py --client-id ID --client-secret SECRET output.json

    # Customise what to back up and where the token cache lives:
    python spotify-backup-v2.py --client-id ID --client-secret SECRET --dump liked,playlists --cache-path /path/to/.cache output.json
"""

import argparse
import json
import logging
import os
import sys

import spotipy
from spotipy.oauth2 import CacheFileHandler, SpotifyOAuth

logging.basicConfig(level=logging.INFO, datefmt="%I:%M:%S", format="[%(asctime)s] %(message)s")
log = logging.getLogger(__name__)

# Scopes required for reading playlists, liked songs/albums, and saved episodes.
SCOPES = "playlist-read-private playlist-read-collaborative user-library-read user-read-playback-position"

DEFAULT_REDIRECT_URI = "http://127.0.0.1:43019/redirect"
DEFAULT_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fetch_all_items(sp, results):
    """Page through a Spotify paged-result object and return all items."""
    items = results["items"]
    while results["next"]:
        results = sp.next(results)
        items.extend(results["items"])
    return items


def _slim_track(track_obj):
    """Return a compact dict for a single track (or episode) inside a playlist/liked list."""
    if track_obj is None:
        return None
    return {
        "name": track_obj.get("name"),
        "uri": track_obj.get("uri"),
        "external_urls": track_obj.get("external_urls", {}),
        "external_ids": track_obj.get("external_ids", {}),
        "artists": [a["name"] for a in track_obj.get("artists", [])],
        "album": track_obj.get("album", {}).get("name"),
        "release_date": track_obj.get("album", {}).get("release_date"),
        "duration_ms": track_obj.get("duration_ms"),
    }


def _slim_episode(episode_obj):
    """Return a compact dict for a saved episode."""
    if episode_obj is None:
        return None
    return {
        "name": episode_obj.get("name"),
        "uri": episode_obj.get("uri"),
        "external_urls": episode_obj.get("external_urls", {}),
        "external_ids": episode_obj.get("external_ids", {}),
        "show": episode_obj.get("show", {}).get("name"),
        "release_date": episode_obj.get("release_date"),
        "duration_ms": episode_obj.get("duration_ms"),
        "description": (episode_obj.get("description") or "")[:200],
    }


def _slim_album(album_obj):
    """Return a compact dict for a saved album."""
    if album_obj is None:
        return None
    return {
        "name": album_obj.get("name"),
        "uri": album_obj.get("uri"),
        "external_urls": album_obj.get("external_urls", {}),
        "external_ids": album_obj.get("external_ids", {}),
        "artists": [a["name"] for a in album_obj.get("artists", [])],
        "release_date": album_obj.get("release_date"),
        "total_tracks": album_obj.get("total_tracks"),
    }


def _slim_show(show_obj):
    """Return a compact dict for a followed podcast (show)."""
    if show_obj is None:
        return None
    return {
        "name": show_obj.get("name"),
        "uri": show_obj.get("uri"),
        "external_urls": show_obj.get("external_urls", {}),
        "publisher": show_obj.get("publisher"),
        "description": (show_obj.get("description") or "")[:300],
        "total_episodes": show_obj.get("total_episodes"),
        "languages": show_obj.get("languages", []),
    }


# ---------------------------------------------------------------------------
# Backup functions
# ---------------------------------------------------------------------------
def backup_liked_songs(sp):
    """Fetch all liked (saved) tracks."""
    log.info("Loading liked songs...")
    results = sp.current_user_saved_tracks(limit=50)
    items = fetch_all_items(sp, results)
    tracks = [
        {
            "added_at": item["added_at"],
            "track": _slim_track(item.get("track")),
        }
        for item in items
        if item.get("track")
    ]
    log.info(f"  Found {len(tracks)} liked songs")
    return tracks


def backup_liked_albums(sp):
    """Fetch all liked (saved) albums."""
    log.info("Loading liked albums...")
    results = sp.current_user_saved_albums(limit=50)
    items = fetch_all_items(sp, results)
    albums = [
        {
            "added_at": item["added_at"],
            "album": _slim_album(item.get("album")),
        }
        for item in items
        if item.get("album")
    ]
    log.info(f"  Found {len(albums)} liked albums")
    return albums


def backup_playlists(sp, user_id):
    """Fetch all playlists and their tracks."""
    log.info("Loading playlists...")
    results = sp.current_user_playlists(limit=50)
    playlist_metas = fetch_all_items(sp, results)
    log.info(f"  Found {len(playlist_metas)} playlists")

    playlists = []
    for pm in playlist_metas:
        if pm is None:
            continue
        name = pm["name"]
        total = pm["tracks"]["total"]
        log.info(f"  Loading playlist: {name} ({total} tracks)")

        track_results = sp.playlist_items(pm["id"], limit=100)
        track_items = fetch_all_items(sp, track_results)

        tracks = []
        for ti in track_items:
            t = ti.get("track")
            if t is None:
                continue
            tracks.append(
                {
                    "added_at": ti.get("added_at"),
                    "track": _slim_track(t),
                }
            )

        playlists.append(
            {
                "id": pm["id"],
                "name": name,
                "description": pm.get("description", ""),
                "owner": pm.get("owner", {}).get("display_name"),
                "public": pm.get("public"),
                "collaborative": pm.get("collaborative"),
                "total_tracks": total,
                "tracks": tracks,
            }
        )

    return playlists


def backup_episodes(sp):
    """Fetch all saved episodes."""
    log.info("Loading saved episodes...")
    results = sp.current_user_saved_episodes(limit=50)
    items = fetch_all_items(sp, results)
    episodes = [
        {
            "added_at": item["added_at"],
            "episode": _slim_episode(item.get("episode")),
        }
        for item in items
        if item.get("episode")
    ]
    log.info(f"  Found {len(episodes)} saved episodes")
    return episodes


def backup_shows(sp):
    """Fetch all followed podcasts (shows)."""
    log.info("Loading followed podcasts...")
    results = sp.current_user_saved_shows(limit=50)
    items = fetch_all_items(sp, results)
    shows = [
        {
            "added_at": item["added_at"],
            "show": _slim_show(item.get("show")),
        }
        for item in items
        if item.get("show")
    ]
    log.info(f"  Found {len(shows)} followed podcasts")
    return shows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Back up Spotify liked songs, playlists and episodes to JSON using spotipy."
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Run interactive authentication (opens browser). Only needed once to create the token cache.",
    )
    parser.add_argument(
        "--client-id",
        required=True,
        help="Spotify app Client ID",
    )
    parser.add_argument(
        "--client-secret",
        required=True,
        help="Spotify app Client Secret",
    )
    parser.add_argument(
        "--redirect-uri",
        default=DEFAULT_REDIRECT_URI,
        help=f"OAuth redirect URI (default: {DEFAULT_REDIRECT_URI})",
    )
    parser.add_argument(
        "--cache-path",
        default=DEFAULT_CACHE_PATH,
        help=f"Path to the token cache file for this user (default: {DEFAULT_CACHE_PATH})",
    )
    parser.add_argument(
        "--dump",
        default="liked,playlists,episodes,shows",
        help="Comma-separated list of what to back up: liked, playlists, episodes, shows (default: all)",
    )
    parser.add_argument("file", help="Output JSON filename")
    args = parser.parse_args()

    sections = [s.strip() for s in args.dump.split(",")]

    # Build the OAuth manager.
    # --auth opens a browser for the initial login; without it the script
    # runs headless using the cached refresh token.
    cache_handler = CacheFileHandler(cache_path=args.cache_path)
    auth_manager = SpotifyOAuth(
        client_id=args.client_id,
        client_secret=args.client_secret,
        redirect_uri=args.redirect_uri,
        scope=SCOPES,
        cache_handler=cache_handler,
        open_browser=args.auth,
    )

    # When running headless, verify that a cached token exists.
    if not args.auth:
        token_info = cache_handler.get_cached_token()
        if not token_info:
            log.error(
                "No cached token found. Run once with --auth to authenticate "
                "interactively, then re-run without --auth for automated use."
            )
            sys.exit(1)

    sp = spotipy.Spotify(auth_manager=auth_manager)

    me = sp.current_user()
    log.info(f"Logged in as {me['display_name']} ({me['id']})")

    backup = {}

    if "liked" in sections:
        backup["liked_songs"] = backup_liked_songs(sp)
        backup["liked_albums"] = backup_liked_albums(sp)

    if "playlists" in sections:
        backup["playlists"] = backup_playlists(sp, me["id"])

    if "episodes" in sections:
        backup["episodes"] = backup_episodes(sp)

    if "shows" in sections:
        backup["shows"] = backup_shows(sp)

    # Write output
    with open(args.file, "w", encoding="utf-8") as f:
        json.dump(backup, f, indent=2, ensure_ascii=False)

    log.info(f"Backup written to {args.file}")


if __name__ == "__main__":
    main()
