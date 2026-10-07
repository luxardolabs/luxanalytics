# luxarch:pagination asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit pagination`.
# luxarch:pagination helper v1 - DO NOT edit the marker line above (fw.view_pagination finds it).
"""The canonical pagination state + URL helper. `luxarch --emit pagination`.

Drop this in at `app/core/pagination.py` and register `paginated_url` as a Jinja global. Emitted,
not hand-written: re-emit to update. The macros are `luxarch --emit pagination-macros`; the worked
chain is `luxarch --emit pagination-example`; the doctrine is `luxarch --playbook pagination`.

## Why this is emitted

Five rules already guarded pagination (`fw.view_pagination`, `fw.no_hand_rolled_pagination`,
`fw.template_pagination`, `fw.crud_paginated_signature`, `fw.paginated_result_unpacked`) and the
playbook said "copy the code from here". Copying IS the drift mechanism: the playbook gave
`paginated_url` and the `<th>` macros as literal code but only NAMED `build_pagination()` and
`pagination_controls` — so the two hardest pieces were the two every repo had to invent, and every
repo invented them differently. One fleet app had a macro taking `total_pages` as a parameter (so
every caller computed it), `start`/`end` arithmetic inside the template, a `calculate_pagination()`
in a core service, and `total_pages` recomputed in dozens of views: ~129 reds from one missing
primitive. Guards before an emit is backwards; this closes it.

## The two invariants the rules are actually protecting

1. **Page arithmetic happens exactly once.** Not in the view, not in the template, not per caller —
   here, in `build_pagination`. A `total_pages` computed in two places disagrees at some boundary,
   and the off-by-one shows up as an empty last page.
2. **Every table URL is built by one function.** A sort link and a page link that each concatenate
   their own query string will drop each other's state: sort, and the filters vanish; page, and the
   sort resets. That bug is invisible in review and obvious to a user on click two.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

# The page size every table defaults to. Override per call, not per repo-fork of this file.
DEFAULT_PER_PAGE = 25


@dataclass(frozen=True)
class PaginationState:
    """Everything a pagination control needs, computed once.

    Frozen on purpose: a template or view that can mutate page state will eventually recompute part
    of it, which is the drift `fw.view_pagination` exists to catch. The field list is the fleet DTO
    named in `--playbook pagination` — do not add derived fields at a call site, add them here.
    """

    page: int
    per_page: int
    total: int
    total_pages: int
    range_start: int  # 1-based index of the first row on this page (0 when empty)
    range_end: int  # 1-based index of the last row on this page (0 when empty)
    has_prev: bool
    has_next: bool
    prev_page: int
    next_page: int

    @property
    def is_empty(self) -> bool:
        return self.total == 0

    @property
    def pages(self) -> list[int]:
        """A compact page window: first, last, and up to two either side of the current page.

        Returned as a plain ascending list; the macro inserts the gaps. A full 1..N list is what
        makes a 4,000-row table render 160 links, so the window lives here rather than being
        re-derived (differently) per template."""
        if self.total_pages <= 7:
            return list(range(1, self.total_pages + 1))
        near = {1, self.total_pages}
        near |= {
            p for p in range(self.page - 2, self.page + 3) if 1 <= p <= self.total_pages
        }
        return sorted(near)


def build_pagination(
    *, page: int, total: int, per_page: int = DEFAULT_PER_PAGE
) -> PaginationState:
    """The ONE place page arithmetic happens. Call it in the view service; pass the result down.

    Clamps defensively rather than trusting its inputs, because `page` arrives from a query string:
    a negative page, a page past the end, or a zero `per_page` are all reachable from a URL a user
    can edit. Each would otherwise surface as an empty table or a division error.
    """
    per_page = max(1, per_page)
    total = max(0, total)
    total_pages = max(1, -(-total // per_page))  # ceil without float rounding
    page = min(max(1, page), total_pages)
    first = (page - 1) * per_page
    return PaginationState(
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        range_start=0 if total == 0 else first + 1,
        range_end=min(first + per_page, total),
        has_prev=page > 1,
        has_next=page < total_pages,
        prev_page=max(1, page - 1),
        next_page=min(total_pages, page + 1),
    )


def skip_limit(page: int, per_page: int = DEFAULT_PER_PAGE) -> tuple[int, int]:
    """`(skip, limit)` for the crud call — the fleet's paginated crud signature is
    `(*, skip, limit) -> tuple[list[...], int]` (`fw.crud_paginated_signature`). Kept here so the
    offset is derived from the same clamped arithmetic as the state, not recomputed at the call site.
    """
    per_page = max(1, per_page)
    return (max(1, page) - 1) * per_page, per_page


def paginated_url(
    base_url: str, query_params: dict[str, str] | None = None, **overrides: str
) -> str:
    """Preserve the page's existing query params, apply overrides (sort_by/sort_order/page),
    strip empty/None so URLs stay clean. Used by BOTH sort headers and pagination links.

    Register as a Jinja global — it takes `base_url` as an ARGUMENT and only appends a query
    string, so it owns no route knowledge and is not the global `fw.url_builder_not_in_template`
    bans (that rule flags a global whose body builds a /-rooted URL or calls a route resolver).
    """
    params = dict(query_params or {})
    params.update(overrides)
    filtered = {k: str(v) for k, v in params.items() if v is not None and v != ""}
    return f"{base_url}?{urlencode(filtered)}" if filtered else base_url
