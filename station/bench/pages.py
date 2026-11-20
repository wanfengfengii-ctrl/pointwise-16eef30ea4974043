from station.bench.confirmation import PageConfirmation
from station.bench.recording import PageKey, PageReply, PageSeal


class PageLedger:
    """Device-side bookkeeping for physical cache pages."""

    def __init__(self, device, replies, exposures, release_edges, *, checkpoint=None):
        self._device = device
        self._replies = replies
        self._exposures = exposures
        self._release_edges = release_edges
        self._checkpoint = checkpoint
        self._generation: dict[int, int] = {}
        self._open: dict[int, PageKey] = {}
        self._known: set[PageKey] = set()
        self._members: dict[PageKey, list[str]] = {}
        self._sealed: dict[PageKey, PageSeal] = {}
        self._confirmations: dict[PageKey, PageConfirmation] = {}

    def open(self, slot):
        if type(slot) is not int or slot < 0:
            raise ValueError('page slot must be a nonnegative integer')
        if slot in self._open:
            raise ValueError(f'slot {slot!r} already holds an unreclaimed page')
        generation = self._generation.get(slot, 0) + 1
        page = PageKey(self._device, slot, generation)
        self._generation[slot] = generation
        self._open[slot] = page
        self._known.add(page)
        self._members[page] = []
        return page

    def page_of(self, exposure_id):
        return self._replies.page_of(exposure_id)

    def record(self, exposure_id, raw_code, page):
        if not isinstance(page, PageKey):
            raise ValueError('page registration requires a page key')
        if page not in self._known:
            raise KeyError(page)
        if self._open.get(page.slot) != page:
            raise ValueError('page is not the open page of its slot')
        seal = self._sealed.get(page)
        members = self._members[page]
        assigned = self._replies.page_of(exposure_id)
        if assigned is not None and assigned != page:
            raise ValueError('exposure already belongs to another page')
        if seal is not None and exposure_id not in members:
            raise ValueError('cannot add a member to a sealed page')
        raw_reply = self._replies.register_page(exposure_id, raw_code, page)
        if exposure_id not in members:
            members.append(exposure_id)
        return PageReply(page, raw_reply)

    def seal(self, page):
        if not isinstance(page, PageKey):
            raise ValueError('expected a page key')
        sealed = self._sealed.get(page)
        if sealed is not None:
            return sealed
        if page not in self._known:
            raise KeyError(page)
        if self._open.get(page.slot) != page:
            raise ValueError('page is not open')
        members = self._members[page]
        if not members:
            raise ValueError('cannot seal an empty page')
        sealed = PageSeal(page, tuple(members))
        self._sealed[page] = sealed
        return sealed

    def deliver_seal(self, page):
        if not isinstance(page, PageKey):
            raise ValueError('expected a page key')
        sealed = self._sealed.get(page)
        if sealed is not None:
            return sealed
        if page not in self._known:
            raise KeyError(page)
        raise ValueError('page has not been sealed')

    def confirm_page(self, page):
        if not isinstance(page, PageKey):
            raise ValueError('expected a page key')
        retained = self._confirmations.get(page)
        if retained is not None:
            return retained
        sealed = self._sealed.get(page)
        if sealed is None:
            if page not in self._known:
                raise KeyError(page)
            raise ValueError('page has not been sealed')
        if self._open.get(page.slot) != page:
            raise ValueError('page is not open')
        self._visit_checkpoint('source.before_confirm')
        for exposure_id in sealed.exposures:
            self._replies.release(exposure_id)
            self._exposures.release_details(exposure_id)
            self._release_edges(exposure_id)
        self._open.pop(page.slot, None)
        confirmation = PageConfirmation(page, sealed.exposures)
        self._confirmations[page] = confirmation
        self._visit_checkpoint('source.after_reclaim')
        return confirmation

    def _visit_checkpoint(self, point):
        if self._checkpoint is not None:
            self._checkpoint(point)
