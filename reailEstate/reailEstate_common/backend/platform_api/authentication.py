"""Session authentication that distinguishes expiry (401) from denial (403).

Keep settings-loaded authenticators independent of APIView to avoid DRF's
schema/settings import cycle during a fresh service startup.
"""
from rest_framework.authentication import SessionAuthentication


class AdminSessionAuthentication(SessionAuthentication):
    def authenticate_header(self, request):
        return "Session"
