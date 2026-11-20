from station.capture.acknowledge import AcknowledgementWorker
from station.capture.admit import CaptureAdmission
from station.capture.page_acknowledge import PageAcknowledgements, PageCollector
from station.capture.page_store import PageStore
from station.capture.pending import ConfirmationQueue
from station.capture.raw_store import RawStore
from station.handover.lookup import ResultLookup
from station.handover.sheet import ShiftHandover
from station.quality.issue import QualityIssuer
from station.quality.result_store import ResultStore
from station.setup.database import StationDatabase
from station.setup.registry import ShiftRegistry


class Station:
    def __init__(self, path, *, checkpoint=None):
        self.database = StationDatabase(path, checkpoint=checkpoint)
        self.shifts = ShiftRegistry(self.database)
        self.raw = RawStore()
        self.confirmations = ConfirmationQueue()
        self.results = ResultStore()
        self.pages = PageStore(self.database, self.raw)
        try:
            self.database.initialize(
                self.shifts.install_schema,
                self.raw.install_schema,
                self.confirmations.install_schema,
                self.results.install_schema,
                self.pages.install_schema,
            )
        except BaseException:
            self.database.close()
            raise
        self.quality = QualityIssuer(self.database, self.shifts, self.raw, self.results)
        self.lookup = ResultLookup(self.database, self.results, self.raw)
        self.handover = ShiftHandover(self.database, self.shifts, self.results, self.lookup)

    def attach(self, source):
        worker = AcknowledgementWorker(self.database, self.confirmations, source)
        ack = PageAcknowledgements(self.database, self.pages, source, worker)
        return CaptureAdmission(self.database, self.raw, self.confirmations, source, ack, self.pages)

    def collect_page(self, page):
        return PageCollector(self.database, self.pages).collect_page(page)

    def close(self):
        self.database.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

