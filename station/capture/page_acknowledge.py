from station.bench.confirmation import PageConfirmation
from station.capture.page_store import validate_page


class PageAcknowledgementWorker:
    """Drives whole-page reclaim by explicit page confirmation."""

    def __init__(self, database, pages, source):
        self._database = database
        self._pages = pages
        self._source = source

    def confirm_page(self, page):
        validate_page(page)
        with self._database.read() as connection:
            if not self._pages.known(connection, page):
                raise KeyError(page)
            retained = self._retained(connection, page)
            if retained is not None:
                return retained
            seal = self._pages.seal(connection, page)
            if seal is None:
                return None
            if self._pages.missing_members(connection, page, seal):
                return None
        confirmation = self._source.confirm_page(page)
        if not isinstance(confirmation, PageConfirmation):
            raise ValueError('device did not return a page confirmation')
        if confirmation.page != page or confirmation.exposures != seal.exposures:
            raise ValueError('device page confirmation differs from the admitted declaration')
        with self._database.write('page_confirm') as connection:
            self._pages.mark_confirmed(connection, page)
        return confirmation

    def _retained(self, connection, page):
        if not self._pages.known(connection, page):
            raise KeyError(page)
        seal = self._pages.seal(connection, page)
        if seal is None:
            return None
        if not self._pages.confirmed(connection, page):
            return None
        return PageConfirmation(page, seal.exposures)


class PageAcknowledgements:
    """Expose single-exposure and whole-page confirmation through one handle."""

    def __init__(self, database, pages, source, workers):
        self._workers = workers
        self._page_worker = PageAcknowledgementWorker(database, pages, source)

    def confirm_exposure(self, exposure_id):
        return self._workers.confirm_exposure(exposure_id)

    def retry_pending(self):
        return self._workers.retry_pending()

    def confirm_page(self, page):
        return self._page_worker.confirm_page(page)


class PageCollector:
    """Read-only view over the persisted page inputs."""

    def __init__(self, database, pages):
        self._database = database
        self._pages = pages

    def collect_page(self, page):
        validate_page(page)
        with self._database.read() as connection:
            if not self._pages.known(connection, page):
                raise KeyError(page)
            seal = self._pages.seal(connection, page)
            if seal is None:
                return None
            readings = self._pages.readings(connection, seal)
            if any(reading is None for reading in readings):
                return None
            return seal, tuple(readings)
