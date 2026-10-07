"""Response policies shared by API adapters."""


class NoStoreResponseMixin:
    cache_control = "private, no-store"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = self.cache_control
        return response
