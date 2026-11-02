from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from .access import profile


class AccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if request.user.is_authenticated and not path.startswith('/static/'):
            p = profile(request.user)
            if not request.user.is_superuser and (not p or not p.approved):
                logout(request)
                return HttpResponseForbidden('Доступ не подтверждён администратором.')
            if path not in ['/login/', '/logout/', '/language/'] and not path.startswith('/security/'):
                enabled = bool(p and p.totp_enabled)
                if (enabled or settings.MFA_REQUIRED) and not request.session.get('mfa_verified'):
                    return redirect('security')
        response = self.get_response(request)
        if not path.startswith('/static/'):
            response['Cache-Control'] = 'no-store, private'
            response['Pragma'] = 'no-cache'
            response['Referrer-Policy'] = 'same-origin'
            response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response
