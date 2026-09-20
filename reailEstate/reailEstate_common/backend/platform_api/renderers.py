import re

from rest_framework.renderers import JSONRenderer


_SNAKE_CASE = re.compile(r"_([a-zA-Z])")


def _camelize(value):
    if isinstance(value, dict):
        return {_SNAKE_CASE.sub(lambda match: match.group(1).upper(), str(key)): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    if isinstance(value, tuple):
        return [_camelize(item) for item in value]
    return value


class CamelCaseJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        return super().render(_camelize(data), accepted_media_type, renderer_context)
