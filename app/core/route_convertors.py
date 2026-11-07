"""Path convertors for the apps routes, registered on import.

`{app_id:appid}` takes everything between the routes' fixed parts, slashes included, because an
app_id can hold a "/". Starlette's own `path` convertor matches `.*`, which stops at a newline
(and `$` then accepts a trailing one), so an id ending in a newline would reach the route cut short
and act on another app: this one matches every character.

It also matches an id that ends in "/". Were it to refuse one, the router would answer PUT
`/apps/victim%2F` with a slash redirect (a 307, which repeats the method) to `/apps/victim`, and
the update or delete would land on another app; matching it makes that request an unknown app id.
"""

from starlette.convertors import Convertor, register_url_convertor


class AppIdConvertor(Convertor[str]):
    regex = r"[\s\S]*"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("appid", AppIdConvertor())

# The path parameter of an app route: use as f"/{APP_ID_PARAM}/edit".
APP_ID_PARAM = "{app_id:appid}"


class SdkIdConvertor(Convertor[str]):
    """An SDK-chosen id (user, session) is any text. Starlette's `path` convertor stops at a
    newline, and `$` then accepts a trailing one, so `victim%0A` reached its route as `victim` and
    opened another row's panel: this one matches every character, slashes and newlines included."""

    regex = r"[\s\S]*"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("sdkid", SdkIdConvertor())

# The path parameters of the user and session panel routes.
USER_ID_PARAM = "{user_id:sdkid}"
SESSION_ID_PARAM = "{session_id:sdkid}"
