"""Microsoft Graph file download/upload, supporting personal OneDrive files.

Uses file APIs because Graph's Excel workbook APIs exclude OneDrive Consumer.
"""
import base64
import os
import time
from urllib.parse import quote, urlparse
import requests


class DriveError(RuntimeError):
    pass


def checked(response, context):
    if not response.ok:
        raise DriveError(f'{context} failed (HTTP {response.status_code}); response details withheld.')
    return response


def read_request(method, url, **kwargs):
    """Retry bounded read-only transport failures without exposing signed URLs."""
    for attempt in range(3):
        try:
            response = requests.request(method, url, **kwargs)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 2:
                raise DriveError('OneDrive read failed after temporary connection errors.') from None
        else:
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                return response
        time.sleep(2 ** attempt)


class OneDrive:
    def __init__(self):
        client = os.environ.get('MS_CLIENT_ID')
        refresh = os.environ.get('MS_REFRESH_TOKEN')
        if not client or not refresh:
            raise DriveError('Missing MS_CLIENT_ID or MS_REFRESH_TOKEN for OneDrive.')
        tenant = os.environ.get('MS_TENANT_ID', 'common')
        if not all(c.isalnum() or c in '-.' for c in tenant):
            raise DriveError('Invalid Microsoft tenant.')
        payload = {'client_id': client, 'refresh_token': refresh, 'grant_type': 'refresh_token',
                   'scope': 'https://graph.microsoft.com/Files.ReadWrite offline_access'}
        if os.environ.get('MS_CLIENT_SECRET'):
            payload['client_secret'] = os.environ['MS_CLIENT_SECRET']
        response = checked(requests.post('https://login.microsoftonline.com/' + tenant + '/oauth2/v2.0/token',
                                         data=payload, timeout=45), 'Microsoft authentication')
        result = response.json()
        self.token = result['access_token']
        # A newly issued refresh token is sensitive. Never write it to a report or log.

    def request(self, method, path, **kwargs):
        headers = {'Authorization': 'Bearer ' + self.token, **kwargs.pop('headers', {})}
        requester = read_request if method == 'GET' else requests.request
        response = requester(method, 'https://graph.microsoft.com/v1.0' + path,
                                    headers=headers, timeout=90, allow_redirects=False, **kwargs)
        return checked(response, 'OneDrive ' + method)

    def read(self, sharing_url):
        encoded = 'u!' + base64.urlsafe_b64encode(sharing_url.encode()).decode().rstrip('=')
        item = self.request('GET', '/shares/' + encoded + '/driveItem').json()
        if item.get('remoteItem'):
            item = item['remoteItem']
        drive_id = item.get('parentReference', {}).get('driveId')
        if not drive_id or not item.get('id'):
            raise DriveError('Could not resolve OneDrive workbook identity.')
        path = '/drives/' + quote(drive_id, safe='') + '/items/' + quote(item['id'], safe='')
        item = self.request('GET', path).json()
        if not item.get('eTag') or not item.get('@microsoft.graph.downloadUrl'):
            raise DriveError('OneDrive did not provide a version or download URL.')
        if item.get('size', 0) > 25_000_000:
            raise DriveError('Workbook exceeds the 25 MB email/update limit.')
        download = item['@microsoft.graph.downloadUrl']
        renewed = False
        for attempt in range(7):
            url = urlparse(download)
            if url.scheme != 'https' or not (url.hostname == 'my.microsoftpersonalcontent.com' or any((url.hostname or '').endswith(suffix)
                    for suffix in ('.1drv.com', '.sharepoint.com', '.live.com', '.onedrive.com'))):
                # Only the hostname is safe to log; the full download URL is signed.
                raise DriveError('Unexpected OneDrive download host: ' + (url.hostname or '(missing)'))
            response = read_request('GET', download, timeout=90, allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                download = response.headers.get('Location', '')
                continue
            if response.status_code in (401, 403) and not renewed:
                # Sharing metadata can contain an expired preauthenticated URL.
                # Ask Graph for a new content redirect; never send Graph bearer
                # credentials to a signed download host.
                renewed = True
                fresh = self.request('GET', path + '/content', headers={'Cache-Control': 'no-cache'})
                if fresh.status_code in (301, 302, 303, 307, 308):
                    download = fresh.headers.get('Location', '')
                    continue
                response = fresh
            checked(response, 'Workbook download')
            if len(response.content) > 25_000_000:
                raise DriveError('Workbook exceeds the download limit.')
            return {'path': path, 'etag': item['eTag'], 'content': response.content}
        raise DriveError('Too many OneDrive download redirects.')

    def write(self, snapshot, content):
        # If somebody edited the workbook after download, Graph returns 412.
        response = self.request('PUT', snapshot['path'] + '/content', data=content,
            headers={'If-Match': snapshot['etag'],
                     'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
        return response.json().get('eTag')
