from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .commute_locations import CommuteLocationError, resolve_location, search_locations

from .api_common import NoStoreResponseMixin


class CommuteLocationThrottle(UserRateThrottle):
    scope = "commute_locations"
    rate = "20/min"


class PrivateCommuteLocationView(NoStoreResponseMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle, CommuteLocationThrottle]

    def lookup_response(self, request, lookup):
        try:
            return Response(lookup(request.data))
        except CommuteLocationError as exc:
            return Response({
                "type": f"https://realestate.invalid/problems/{exc.code.lower().replace('_', '-')}",
                "title": "Commute location lookup failed",
                "status": exc.status,
                "code": exc.code,
                "detail": exc.detail,
                "correlationId": str(getattr(request, "correlation_id", "")),
            }, status=exc.status, content_type="application/problem+json")


class CommuteLocationSearchView(PrivateCommuteLocationView):
    def post(self, request):
        return self.lookup_response(request, search_locations)


class CommuteLocationResolveView(PrivateCommuteLocationView):
    def post(self, request):
        return self.lookup_response(request, resolve_location)
