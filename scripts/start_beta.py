"""Portable masked-credential launcher. No files or tokens are persisted."""
import argparse
import getpass
import os


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--enable-paid-beta',action='store_true')
    a=p.parse_args()
    if not os.environ.get('DATABASE_URL'):p.error('Set the restricted DATABASE_URL first.')
    if a.enable_paid_beta and (not os.environ.get('GMAIL_SENDER') or not os.environ.get('GOOGLE_CLIENT_JSON_PATH')):
        p.error('Set GMAIL_SENDER and the external GOOGLE_CLIENT_JSON_PATH first.')
    try:
        os.environ['TELEGRAM_BOT_TOKEN']=getpass.getpass('Telegram bot token: ')
        args=['--connect']
        if a.enable_paid_beta:
            os.environ['OPENAI_API_KEY']=getpass.getpass('OpenAI API key: ')
            args+=['--enable-paid-beta','--client-json',os.environ['GOOGLE_CLIENT_JSON_PATH']]
        from ai_tender_intelligence.beta67.__main__ import main as run
        return run(args)
    finally:
        os.environ.pop('TELEGRAM_BOT_TOKEN',None)
        os.environ.pop('OPENAI_API_KEY',None)


if __name__=='__main__':raise SystemExit(main())
