"""Production WSGI entry point. A TLS reverse proxy must serve STATIC_ROOT.

Run only after configuring .env, PostgreSQL, MFA, cache table and static files.
"""
import os
from config.wsgi import application
from waitress import serve

if __name__ == '__main__':
    serve(application, host='127.0.0.1', port=int(os.getenv('PORT', '8017')), threads=4,
          trusted_proxy='127.0.0.1', trusted_proxy_count=1,
          trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-host'},
          clear_untrusted_proxy_headers=True)
