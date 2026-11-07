"""Path convertors for the apps routes, registered on import.

`{app_id:appid}` takes everything between the routes' fixed parts, slashes included, because an
app_id can hold a "/". Starlette's own `path` convertor matches `.*`, which stops at a newline
(and `$` then accepts a trailing one), so an id ending in a newline would reach the route cut short
and act on another app: this one matches every character. It does not match an id ending in "/":
`/apps/list/` then falls through to the slash redirect to `/apps/list` instead of being a 405 from
the PUT/DELETE route, and no app can be created with such an id.
"""

from starlette.convertors import Convertor, register_url_convertor


class AppIdConvertor(Convertor[str]):
    regex = r"(?:[\s\S]*[^/])?"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("appid", AppIdConvertor())

# The path parameter of an app route: use as f"/{APP_ID_PARAM}/edit".
APP_ID_PARAM = "{app_id:appid}"
