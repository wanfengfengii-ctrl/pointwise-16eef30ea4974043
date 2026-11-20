from dataclasses import dataclass

from station.setup.roster import validate_identifier


@dataclass(frozen=True, slots=True)
class RawReply:
    exposure_id: str
    shift_id: str
    specimen_id: str
    head: str
    raw_code: int


@dataclass(frozen=True, slots=True)
class PageKey:
    device: str
    slot: int
    generation: int

    def __post_init__(self):
        validate_identifier(self.device, 'device namespace')
        if type(self.slot) is not int or self.slot < 0:
            raise ValueError('page slot must be a nonnegative integer')
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError('page generation must be a positive integer')


@dataclass(frozen=True, slots=True)
class PageReply:
    page: PageKey
    reply: RawReply

    def __post_init__(self):
        if not isinstance(self.page, PageKey):
            raise ValueError('page reply requires a page key')
        if not isinstance(self.reply, RawReply):
            raise ValueError('page reply requires an original raw reply')


def validate_exposure_tuple(exposures, label='page exposure'):
    if type(exposures) is not tuple:
        raise ValueError(f'{label} list must be a tuple')
    if not exposures:
        raise ValueError(f'{label} list is empty')
    for exposure_id in exposures:
        validate_identifier(exposure_id, 'exposure ID')
    if len(set(exposures)) != len(exposures):
        raise ValueError(f'{label} list has duplicates')
    return exposures


@dataclass(frozen=True, slots=True)
class PageSeal:
    page: PageKey
    exposures: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.page, PageKey):
            raise ValueError('page seal requires a page key')
        validate_exposure_tuple(self.exposures)


class ReplyBuffer:
    def __init__(self, exposures):
        self._exposures = exposures
        self._replies: dict[str, RawReply] = {}
        self._pages: dict[str, PageKey] = {}

    def record(self, exposure_id, raw_code):
        if type(raw_code) is not int:
            raise ValueError('raw code must be an integer')
        if self.page_of(exposure_id) is not None:
            raise ValueError('exposure was recorded with a page identity')
        return self._register(exposure_id, raw_code)

    def register_page(self, exposure_id, raw_code, page):
        if type(raw_code) is not int:
            raise ValueError('raw code must be an integer')
        if not isinstance(page, PageKey):
            raise ValueError('page registration requires a page key')
        assigned = self._pages.get(exposure_id)
        if assigned is not None and assigned != page:
            raise ValueError('exposure already belongs to another page')
        if exposure_id in self._replies and assigned is None:
            raise ValueError('exposure was recorded without a page identity')
        reply = self._register(exposure_id, raw_code)
        self._pages[exposure_id] = page
        return reply

    def page_of(self, exposure_id):
        return self._pages.get(exposure_id)

    def _register(self, exposure_id, raw_code):
        exposure = self._exposures.find(exposure_id)
        if exposure is None:
            raise KeyError(exposure_id)
        if exposure.phase != 'closed':
            raise ValueError('exposure has not finished')
        previous = self._replies.get(exposure_id)
        if previous is not None:
            if previous.raw_code != raw_code:
                raise ValueError('device reply already has a different raw code')
            return previous
        reply = RawReply(
            exposure.exposure_id, exposure.shift_id, exposure.specimen_id, exposure.head, raw_code
        )
        self._replies[exposure_id] = reply
        return reply

    def get(self, exposure_id):
        reply = self._replies[exposure_id]
        page = self._pages.get(exposure_id)
        if page is None:
            return reply
        return PageReply(page, reply)

    def release(self, exposure_id):
        self._replies.pop(exposure_id, None)
