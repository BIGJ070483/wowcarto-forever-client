"""Fetch the closed-service snapshot using certificate-validated HTTPS.

This client does not install or execute downloaded data. Availability of this
endpoint does not prove native compatibility or a complete synchronisation.
"""
import json
import ssl
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

from tools.community_assembly import assemble_snapshot

SNAPSHOT_URL = 'https://arckangel.pages-perso.free.fr/wowcarto/api.php?action=community_snapshot'
MAX_RESPONSE = 6 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'Redirection refusée', headers, fp)


def fetch_snapshot():
    """Fetch the one configured origin with default CA and hostname checks."""
    request = Request(SNAPSHOT_URL, headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'})
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(request, timeout=15) as response:
        if response.status != 200 or response.headers.get_content_type() != 'application/json':
            raise ValueError('Réponse communautaire non reconnue')
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isascii() or not length.isdigit() or int(length) > MAX_RESPONSE):
            raise ValueError('Réponse communautaire trop volumineuse')
        payload = response.read(MAX_RESPONSE + 1)
        if len(payload) > MAX_RESPONSE:
            raise ValueError('Réponse communautaire trop volumineuse')
        if length is not None and len(payload) != int(length):
            raise ValueError('Réponse communautaire incomplète')
    try:
        result = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('Réponse communautaire invalide') from error
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise ValueError('Réponse communautaire refusée')
    return assemble_snapshot(result.get('snapshot'))
