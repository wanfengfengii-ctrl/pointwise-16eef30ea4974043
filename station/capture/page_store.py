from station.bench.recording import PageKey, PageSeal, validate_exposure_tuple


def validate_page(page):
    if not isinstance(page, PageKey):
        raise ValueError('expected a page key')
    return page


class PageStore:
    """Persisted page membership, seal declarations and reclaim receipts."""

    def __init__(self, database, raw_store):
        self._database = database
        self._raw = raw_store

    @staticmethod
    def install_schema(connection):
        connection.execute('''
            CREATE TABLE IF NOT EXISTS physical_pages (
                page_device TEXT NOT NULL,
                page_slot INTEGER NOT NULL CHECK (page_slot >= 0),
                page_generation INTEGER NOT NULL CHECK (page_generation >= 1),
                PRIMARY KEY (page_device, page_slot, page_generation)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS page_members (
                page_device TEXT NOT NULL,
                page_slot INTEGER NOT NULL,
                page_generation INTEGER NOT NULL,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                exposure_id TEXT NOT NULL UNIQUE REFERENCES raw_readings(exposure_id),
                PRIMARY KEY (page_device, page_slot, page_generation, ordinal),
                UNIQUE (page_device, page_slot, page_generation, exposure_id),
                FOREIGN KEY (page_device, page_slot, page_generation)
                    REFERENCES physical_pages(page_device, page_slot, page_generation)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS page_seals (
                page_device TEXT NOT NULL,
                page_slot INTEGER NOT NULL,
                page_generation INTEGER NOT NULL,
                PRIMARY KEY (page_device, page_slot, page_generation),
                FOREIGN KEY (page_device, page_slot, page_generation)
                    REFERENCES physical_pages(page_device, page_slot, page_generation)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS page_seal_members (
                page_device TEXT NOT NULL,
                page_slot INTEGER NOT NULL,
                page_generation INTEGER NOT NULL,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                exposure_id TEXT NOT NULL,
                PRIMARY KEY (page_device, page_slot, page_generation, ordinal),
                UNIQUE (page_device, page_slot, page_generation, exposure_id),
                FOREIGN KEY (page_device, page_slot, page_generation)
                    REFERENCES page_seals(page_device, page_slot, page_generation)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS page_confirmations (
                page_device TEXT NOT NULL,
                page_slot INTEGER NOT NULL,
                page_generation INTEGER NOT NULL,
                PRIMARY KEY (page_device, page_slot, page_generation),
                FOREIGN KEY (page_device, page_slot, page_generation)
                    REFERENCES physical_pages(page_device, page_slot, page_generation)
            )
        ''')

    # -- identity ---------------------------------------------------------

    @staticmethod
    def _fields(page):
        return (page.device, page.slot, page.generation)

    def known(self, connection, page):
        row = connection.execute('''
            SELECT 1 FROM physical_pages
            WHERE page_device = ? AND page_slot = ? AND page_generation = ?
        ''', self._fields(page)).fetchone()
        return row is not None

    def touch(self, connection, page):
        connection.execute('''
            INSERT OR IGNORE INTO physical_pages (page_device, page_slot, page_generation)
            VALUES (?, ?, ?)
        ''', self._fields(page))

    def page_of(self, connection, exposure_id):
        row = connection.execute('''
            SELECT page_device, page_slot, page_generation FROM page_members
            WHERE exposure_id = ?
        ''', (exposure_id,)).fetchone()
        if row is None:
            return None
        return PageKey(row['page_device'], row['page_slot'], row['page_generation'])

    # -- membership -------------------------------------------------------

    def member_positions(self, connection, page):
        rows = connection.execute('''
            SELECT exposure_id FROM page_members
            WHERE page_device = ? AND page_slot = ? AND page_generation = ?
            ORDER BY ordinal
        ''', self._fields(page))
        return tuple(row['exposure_id'] for row in rows)

    def add_member(self, connection, page, exposure_id):
        self.touch(connection, page)
        row = connection.execute('''
            SELECT coalesce(max(ordinal), -1) AS last FROM page_members
            WHERE page_device = ? AND page_slot = ? AND page_generation = ?
        ''', self._fields(page)).fetchone()
        connection.execute('''
            INSERT INTO page_members
                (page_device, page_slot, page_generation, ordinal, exposure_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (*self._fields(page), row['last'] + 1, exposure_id))

    # -- seal -------------------------------------------------------------

    def seal(self, connection, page):
        row = connection.execute('''
            SELECT 1 FROM page_seals
            WHERE page_device = ? AND page_slot = ? AND page_generation = ?
        ''', self._fields(page)).fetchone()
        if row is None:
            return None
        members = connection.execute('''
            SELECT exposure_id FROM page_seal_members
            WHERE page_device = ? AND page_slot = ? AND page_generation = ?
            ORDER BY ordinal
        ''', self._fields(page))
        return PageSeal(page, tuple(entry['exposure_id'] for entry in members))

    def add_seal(self, connection, page, exposures):
        validate_exposure_tuple(exposures)
        self.touch(connection, page)
        connection.execute('''
            INSERT INTO page_seals (page_device, page_slot, page_generation)
            VALUES (?, ?, ?)
        ''', self._fields(page))
        connection.executemany('''
            INSERT INTO page_seal_members
                (page_device, page_slot, page_generation, ordinal, exposure_id)
            VALUES (?, ?, ?, ?, ?)
        ''', ((*self._fields(page), ordinal, exposure_id)
              for ordinal, exposure_id in enumerate(exposures)))

    # -- readiness and reclaim -------------------------------------------

    def missing_members(self, connection, page, seal):
        missing = []
        for exposure_id in seal.exposures:
            if self.page_of(connection, exposure_id) != page:
                missing.append(exposure_id)
            elif self._raw.by_exposure(connection, exposure_id) is None:
                missing.append(exposure_id)
        return tuple(missing)

    def confirmed(self, connection, page):
        row = connection.execute('''
            SELECT 1 FROM page_confirmations
            WHERE page_device = ? AND page_slot = ? AND page_generation = ?
        ''', self._fields(page)).fetchone()
        return row is not None

    def mark_confirmed(self, connection, page):
        self.touch(connection, page)
        connection.execute('''
            INSERT OR IGNORE INTO page_confirmations
                (page_device, page_slot, page_generation)
            VALUES (?, ?, ?)
        ''', self._fields(page))

    def readings(self, connection, seal):
        return tuple(self._raw.by_exposure(connection, exposure_id)
                     for exposure_id in seal.exposures)
