"""Obtain a Gmail send-only refresh token on a trusted local Windows desktop."""

import argparse
import os
from pathlib import Path
import subprocess

from ai_tender_intelligence.demo41 import mail, oauth42


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-json', type=Path, required=True)
    args = parser.parse_args(argv)
    if os.name != 'nt':
        parser.error('Run this helper on the local Windows desktop.')
    grant = None
    try:
        mail._recipient(mail.SENDER)
        if mail.SENDER.endswith('.invalid'):
            raise oauth42.OAuthFailure('GMAIL_SENDER_REQUIRED')
        clipboard = Path(os.environ['SystemRoot']) / 'System32' / 'clip.exe'
        if not clipboard.is_file():
            raise oauth42.OAuthFailure('WINDOWS_CLIPBOARD_UNAVAILABLE')
        grant = oauth42.authorize(args.client_json, Path(__file__).resolve().parents[1], offline=True)
        subprocess.run([str(clipboard)], input=grant.refresh_token.encode('ascii'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
        print('Refresh token issued and copied. Paste it into the deployment secret field, then clear the clipboard.')
        return 0
    except oauth42.OAuthFailure as error:
        print('Local Gmail authorization failed ('+str(error)+').')
        return 2
    except (Exception, KeyboardInterrupt):
        print('Local Gmail authorization or clipboard transfer could not be completed.')
        return 2
    finally:
        if grant:
            grant.close()


if __name__=='__main__':
    raise SystemExit(main())
