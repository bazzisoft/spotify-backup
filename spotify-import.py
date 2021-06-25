#!/usr/bin/env python3
# pylint: skip-file

import argparse
import codecs
import csv
import http.client
import http.server
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import Levenshtein

logging.basicConfig(level=20, datefmt='%I:%M:%S', format='[%(asctime)s] %(message)s')


class SpotifyAPI:

    # Requires an OAuth token.
    def __init__(self, auth):
        self._auth = auth

    # Gets a resource from the Spotify API and returns the object.
    def get(self, url, params={}, tries=3):
        # Construct the correct URL.
        if not url.startswith('https://api.spotify.com/v1/'):
            url = 'https://api.spotify.com/v1/' + url
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)

        # Try the sending off the request a specified number of times before giving up.
        for _ in range(tries):
            try:
                req = urllib.request.Request(url)
                req.add_header('Authorization', 'Bearer ' + self._auth)
                res = urllib.request.urlopen(req)
                reader = codecs.getreader('utf-8')
                return json.load(reader(res))
            except Exception as err:
                logging.info('Couldn\'t load URL: {} ({})'.format(url, err))
                time.sleep(2)
                logging.info('Trying again...')
        sys.exit(1)

    # The Spotify API breaks long lists into multiple pages. This method automatically
    # fetches all pages and joins them, returning in a single list of objects.
    def list(self, url, params={}):
        last_log_time = time.time()
        response = self.get(url, params)
        items = response['items']

        while response['next']:
            if time.time() > last_log_time + 15:
                last_log_time = time.time()
                logging.info(f"Loaded {len(items)}/{response['total']} items")

            response = self.get(response['next'])
            items += response['items']
        return items

    # POST to the Spotify API and returns the object.
    def post(self, url, params={}, data=None, tries=3):
        # Construct the correct URL.
        if not url.startswith('https://api.spotify.com/v1/'):
            url = 'https://api.spotify.com/v1/' + url
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        if data is not None:
            data = json.dumps(data).encode('utf-8')

        # Try the sending off the request a specified number of times before giving up.
        for _ in range(tries):
            try:
                req = urllib.request.Request(url, data=data)
                req.add_header('Authorization', 'Bearer ' + self._auth)
                req.add_header('Content-Type', 'application/json')
                res = urllib.request.urlopen(req)
                reader = codecs.getreader('utf-8')
                return json.load(reader(res))
            except Exception as err:
                logging.info('Couldn\'t load URL: {} ({})'.format(url, err))
                time.sleep(2)
                logging.info('Trying again...')
        sys.exit(1)

    # Pops open a browser window for a user to log in and authorize API access.
    @staticmethod
    def authorize(client_id, scope):
        url = 'https://accounts.spotify.com/authorize?' + urllib.parse.urlencode({
            'response_type': 'token',
            'client_id': client_id,
            'scope': scope,
            'redirect_uri': 'http://127.0.0.1:{}/redirect'.format(SpotifyAPI._SERVER_PORT)
        })
        logging.info(f'Logging in (click if it doesn\'t open automatically): {url}')
        webbrowser.open(url)

        # Start a simple, local HTTP server to listen for the authorization token... (i.e. a hack).
        server = SpotifyAPI._AuthorizationServer('0.0.0.0', SpotifyAPI._SERVER_PORT)
        try:
            while True:
                server.handle_request()
        except SpotifyAPI._Authorization as auth:
            return SpotifyAPI(auth.access_token)

    # The port that the local server listens on. Don't change this,
    # as Spotify only will redirect to certain predefined URLs.
    _SERVER_PORT = 43019

    class _AuthorizationServer(http.server.HTTPServer):
        def __init__(self, host, port):
            http.server.HTTPServer.__init__(self, (host, port), SpotifyAPI._AuthorizationHandler)

        # Disable the default error handling.
        def handle_error(self, request, client_address):
            raise

    class _AuthorizationHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # The Spotify API has redirected here, but access_token is hidden in the URL fragment.
            # Read it using JavaScript and send it to /token as an actual query string...
            if self.path.startswith('/redirect'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<script>location.replace("token?" + location.hash.slice(1));</script>')

            # Read access_token and use an exception to kill the server listening...
            elif self.path.startswith('/token?'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<script>close()</script>Thanks! You may now close this window.')

                access_token = re.search('access_token=([^&]*)', self.path).group(1)
                logging.info(f'Received access token from Spotify: {access_token}')
                raise SpotifyAPI._Authorization(access_token)

            else:
                self.send_error(404)

        # Disable the default logging.
        def log_message(self, format, *args):
            pass

    class _Authorization(Exception):
        def __init__(self, access_token):
            self.access_token = access_token


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def main():
    # Parse arguments.
    parser = argparse.ArgumentParser(description='Imports JSON format to a Spotify playlist. By default, opens a browser window '
                                               + 'to authorize the Spotify Web API, but you can also manually specify'
                                               + ' an OAuth token with the --token option.')
    parser.add_argument('--token', metavar='OAUTH_TOKEN', help='use a Spotify OAuth token (requires the '
                                                             + '`playlist-read-private` permission)')
    parser.add_argument('--import', action='store_true', help='Import to spotify instead of CSV output.')
    parser.add_argument('playlist', help='playlist ID')
    parser.add_argument('file', type=argparse.FileType('r'), help='input filename')
    args = parser.parse_args()

    # Prepare CSV writer
    writer = csv.writer(sys.stdout)

    # Read the JSON
    try:
        data = json.loads(args.file.read())
    except:
        logging.error('Invalid JSON file provided.')
        sys.exit(1)

    # Log into the Spotify API.
    if args.token:
        spotify = SpotifyAPI(args.token)
    else:
        spotify = SpotifyAPI.authorize(client_id='f273705a8fa44a1f9b962c355c5ee6e5',
                                       scope='playlist-read-private playlist-read-collaborative '
                                             'user-library-read playlist-modify-public '
                                             'playlist-modify-private')

    # Get the ID of the logged in user.
    logging.info('Loading user info...')
    me = spotify.get('me')
    logging.info('Logged in as {display_name} ({id})'.format(**me))

    # Search for each track
    to_import = []
    for song in data:
        title = re.sub(r'\(.*?\)', '', song['title']).strip()
        res = spotify.get('search', params={
            'q': f'{song["artist"]} {title}',
            'type': 'track',
            'limit': 1
        })

        if not res['tracks']['items']:
            writer.writerow([title, song['artist'], '', ''])
            continue

        track = res['tracks']['items'][0]
        writer.writerow([
            title, song['artist'],
            track['name'], track['artists'][0]['name'],
            Levenshtein.distance(title, track['name'])
        ])

        to_import.append(track['uri'])

    if getattr(args, 'import', False):
        for tracks in chunks(to_import, 100):
            spotify.post(f'playlists/{args.playlist}/tracks', data={'uris': tracks})


if __name__ == '__main__':
    main()
