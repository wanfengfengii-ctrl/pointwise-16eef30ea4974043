from station.bench.recording import PageReply, PageSeal, RawReply
from station.capture.identity import check_reply_identity, identity_from_exposure
from station.setup.roster import validate_identifier


class CaptureAdmission:
    def __init__(self, database, raw_store, queue, source, acknowledgements, pages=None):
        self._database = database
        self._raw = raw_store
        self._queue = queue
        self._source = source
        self._pages = pages
        self.acknowledgements = acknowledgements

    # -- exposure admission ----------------------------------------------

    def admit(self, reply):
        if isinstance(reply, PageReply):
            return self._admit_page_reply(reply)
        if not isinstance(reply, RawReply):
            raise ValueError('expected an original device reply')
        exposure_id = validate_identifier(reply.exposure_id, 'exposure ID')
        with self._database.write('admit') as connection:
            if self._pages is not None and self._pages.page_of(connection, exposure_id) is not None:
                raise ValueError('page material cannot be admitted without its page identity')
            reading = self._raw.by_exposure(connection, exposure_id)
            if reading is None:
                if self._source.page_of(exposure_id) is not None:
                    raise ValueError('page material cannot be admitted without its page identity')
                reading = self._take_original(connection, reply)
            else:
                check_reply_identity(reply, reading.identity)
                if reply.raw_code != reading.raw_code:
                    raise ValueError(f'conflicting raw code for {exposure_id!r}')
        self.acknowledgements.confirm_exposure(exposure_id)
        return reading.receipt

    def _take_original(self, connection, reply):
        exposure = self._source.exposure(reply.exposure_id)
        if exposure is None:
            raise KeyError(reply.exposure_id)
        identity = identity_from_exposure(exposure)
        check_reply_identity(reply, identity)
        original = self._source.deliver_reply(reply.exposure_id)
        if original != reply:
            raise ValueError(f'reply differs from the buffered original for {reply.exposure_id!r}')
        reading = self._raw.insert(connection, identity, reply.raw_code)
        self._queue.enqueue(connection, reply.exposure_id)
        return reading

    # -- page admission ---------------------------------------------------

    def admit_seal(self, seal: PageSeal):
        if not isinstance(seal, PageSeal):
            raise ValueError('expected a page seal declaration')
        self._require_bound_page(seal.page)
        with self._database.write('admit_seal') as connection:
            stored = self._pages.seal(connection, seal.page)
            if stored is not None:
                if stored != seal:
                    raise ValueError(f'conflicting seal declaration for page {seal.page!r}')
                return stored
            self._check_device_seal(seal)
            self._check_seal_covers_members(connection, seal)
            self._pages.add_seal(connection, seal.page, seal.exposures)
        return seal

    def _admit_page_reply(self, page_reply: PageReply):
        page = page_reply.page
        reply = page_reply.reply
        if not isinstance(reply, RawReply):
            raise ValueError('page reply requires an original raw reply')
        exposure_id = validate_identifier(reply.exposure_id, 'exposure ID')
        self._require_bound_page(page)
        with self._database.write('admit') as connection:
            stored_page = self._pages.page_of(connection, exposure_id)
            if stored_page is not None:
                if stored_page != page:
                    raise ValueError(f'exposure {exposure_id!r} belongs to another page')
                reading = self._raw.by_exposure(connection, exposure_id)
                if reading is None:
                    raise KeyError(exposure_id)
                check_reply_identity(reply, reading.identity)
                if reply.raw_code != reading.raw_code:
                    raise ValueError(f'conflicting raw code for {exposure_id!r}')
                return reading.receipt
            seal = self._pages.seal(connection, page)
            if seal is not None and exposure_id not in seal.exposures:
                raise ValueError(f'exposure {exposure_id!r} is not a member of page {page!r}')
            if self._raw.by_exposure(connection, exposure_id) is not None:
                raise ValueError('exposure was already admitted without its page identity')
            original = self._source.deliver_reply(exposure_id)
            if not isinstance(original, PageReply):
                raise ValueError(f'exposure {exposure_id!r} has no page identity at the device')
            if original.page != page or original.reply != reply:
                raise ValueError(f'reply differs from the buffered page original for {exposure_id!r}')
            exposure = self._source.exposure(exposure_id)
            if exposure is None:
                raise KeyError(exposure_id)
            identity = identity_from_exposure(exposure)
            check_reply_identity(reply, identity)
            reading = self._raw.insert(connection, identity, reply.raw_code)
            self._pages.add_member(connection, page, exposure_id)
        return reading.receipt

    # -- validation helpers -------------------------------------------------

    def _require_bound_page(self, page):
        device = getattr(self._source, 'device', None)
        if device is not None and page.device != device:
            raise ValueError(f'page {page!r} does not belong to the bound device')

    def _check_device_seal(self, seal):
        actual = self._source.deliver_seal(seal.page)
        if actual != seal:
            raise ValueError(f'seal declaration differs from the device for page {seal.page!r}')

    def _check_seal_covers_members(self, connection, seal):
        members = self._pages.member_positions(connection, seal.page)
        extra = tuple(exposure_id for exposure_id in members if exposure_id not in seal.exposures)
        if extra:
            raise ValueError(f'page seal omits admitted members {extra!r}')
        for exposure_id in seal.exposures:
            assigned = self._pages.page_of(connection, exposure_id)
            if assigned is not None and assigned != seal.page:
                raise ValueError(f'seal member {exposure_id!r} belongs to another page')
            if assigned is None and self._raw.by_exposure(connection, exposure_id) is not None:
                raise ValueError(
                    f'seal member {exposure_id!r} was admitted without this page identity'
                )
