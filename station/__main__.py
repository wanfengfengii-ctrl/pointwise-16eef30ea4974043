import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from station import Station
from station.bench.offline import FaultSchedule, OfflineBench
from station.bench.recording import PageReply


SAMPLES = (
    ('n7', 'P', (3, 7), (10, 12, 2, True), ('day-000001', 'day-000002')),
    ('n7.affine_signed', 'Q', (4, 6), (13, 10, -3, False), ('day-000003', 'day-000004')),
)

PAGE_DEVICE = 'page-demo'
PAGE_SLOT = 4
PAGE_CODES = {'P': (3, 7), 'Q': (4, 6), 'R': (5, 9)}


def capture_specimen(bench, capture, specimen, codes):
    bench.emit('mount', shift_id='day', specimen_id=specimen)
    exposures = {head: bench.request_measurement(head) for head in ('left', 'right')}
    for (head, exposure_id), code in zip(exposures.items(), codes, strict=True):
        bench.emit('open', exposure_id=exposure_id, head=head)
        bench.emit('close', exposure_id=exposure_id, head=head)
        bench.record(exposure_id, code)
    bench.emit('unmount')
    for head in ('right', 'left'):
        capture.admit(bench.deliver_reply(exposures[head]))
    if any(bench.adapter.exposure_edges(exposure_id) for exposure_id in exposures.values()):
        raise AssertionError('device did not reclaim exposure edges')


def decision_row(result):
    return [result.left_um, result.right_um, result.difference_um, result.accepted]


def emit(case_id, input_fields, expected, actual, downstream_expected, downstream_actual, code_paths, oracle):
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'demo values differ for {case_id!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case_id,
        'input': input_fields,
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': code_paths,
        'oracle': oracle,
    }, sort_keys=True))


def page_members(bench, page, specimen, codes, shift='day'):
    bench.emit('mount', shift_id=shift, specimen_id=specimen)
    exposures = {head: bench.request_measurement(head) for head in ('left', 'right')}
    members = {}
    for (head, exposure_id), code in zip(exposures.items(), codes, strict=True):
        bench.emit('open', exposure_id=exposure_id, head=head)
        bench.emit('close', exposure_id=exposure_id, head=head)
        members[head] = bench.record(exposure_id, code, page=page)
    bench.emit('unmount')
    return exposures, members


def reading_rows(readings):
    return [[reading.identity.exposure_id, reading.identity.head, reading.raw_code,
             reading.receipt.exposure_id, reading.receipt.number] for reading in readings]


def run_page_demo(directory):
    faults = FaultSchedule()
    page_path = Path(directory) / 'pages.sqlite'
    station = Station(page_path, checkpoint=faults)
    station.shifts.open('day', ('P', 'Q', 'R'), (3, 1), (2, -2), 2)
    bench = OfflineBench(station.shifts, namespace=PAGE_DEVICE, checkpoint=faults)
    capture = station.attach(bench)
    try:
        # -- out-of-order: reply B, replay B, then seal, then reply A -------------
        g1 = bench.open_page(PAGE_SLOT)
        exposures_p, members_p = page_members(bench, g1, 'P', PAGE_CODES['P'])
        deliver_b = bench.deliver_reply(exposures_p['right'])
        if deliver_b != members_p['right']:
            raise AssertionError('page reply is not the recorded original')
        receipt_b = capture.admit(members_p['right'])
        if capture.admit(members_p['right']) != receipt_b:
            raise AssertionError('replayed page reply did not reuse the admission')
        seal_g1 = bench.seal_page(g1)
        if bench.deliver_seal(g1) != seal_g1 or capture.admit_seal(seal_g1) != seal_g1:
            raise AssertionError('page seal did not survive declaration handoff')
        if station.collect_page(g1) is not None:
            raise AssertionError('page must not be complete before every member is admitted')
        try:
            bench.confirm(exposures_p['left'])
        except ValueError:
            pass
        else:
            raise AssertionError('single-exposure confirmation must not reclaim page material')
        if (bench.deliver_reply(exposures_p['left']) != members_p['left']
                or bench.deliver_reply(exposures_p['right']) != members_p['right']):
            raise AssertionError('old single confirmation touched page material')

        faults.arm('admit.before_commit')
        try:
            capture.admit(members_p['left'])
        except Exception:
            pass
        else:
            raise AssertionError('armed admission fault did not fire')
        pending_visible = station.collect_page(g1) is None
        sources_intact = (bench.deliver_reply(exposures_p['left']) == members_p['left']
                          and bench.deliver_reply(exposures_p['right']) == members_p['right'])
        receipt_a = capture.admit(members_p['left'])
        completed_seal, completed_readings = station.collect_page(g1)
        result_p = station.quality.issue('day', 'P')
        emit(
            'page.out_of_order',
            {
                'device': PAGE_DEVICE, 'slot': PAGE_SLOT,
                'raw_codes_left_right': list(PAGE_CODES['P']),
                'reply_order': ['right', 'right', 'left'],
                'declaration_order': ['seal', 'left'],
                'fault': 'admit.before_commit',
            },
            {
                'admission_order': [exposures_p['right'], exposures_p['left']],
                'pending_after_fault': True,
                'sources_intact_after_fault': True,
                'readings': [[exposures_p['left'], 'left', 3], [exposures_p['right'], 'right', 7]],
                'quality': [10, 12, 2, True],
            },
            {
                'admission_order': [receipt_b.exposure_id, receipt_a.exposure_id],
                'pending_after_fault': pending_visible,
                'sources_intact_after_fault': sources_intact,
                'readings': [[row[0], row[1], row[2]] for row in reading_rows(completed_readings)],
                'quality': decision_row(result_p),
            },
            {'seal_exposures': list(seal_g1.exposures)},
            {'seal_exposures': list(completed_seal.exposures)},
            ['station/bench/pages.py', 'station/capture/admit.py',
             'station/capture/page_store.py', 'station/quality/issue.py'],
            'Left and right page members admitted in reverse order; single-exposure reclaim rejected; '
            'quality uses the persisted originals before page reclaim.',
        )

        # -- declaration-first on a second slot ----------------------------------
        g3 = bench.open_page(PAGE_SLOT + 1)
        exposures_r, members_r = page_members(bench, g3, 'R', PAGE_CODES['R'])
        seal_g3 = bench.seal_page(g3)
        capture.admit_seal(seal_g3)
        early = station.collect_page(g3) is None
        for head in ('right', 'left'):
            capture.admit(members_r[head])
        seal_seen, readings_r = station.collect_page(g3)
        emit(
            'page.declaration_first',
            {
                'device': PAGE_DEVICE, 'slot': PAGE_SLOT + 1,
                'raw_codes_left_right': list(PAGE_CODES['R']),
                'declaration_order': ['seal', 'right', 'left'],
            },
            {
                'incomplete_after_seal_only': True,
                'readings': [[exposures_r['left'], 'left', 5], [exposures_r['right'], 'right', 9]],
            },
            {
                'incomplete_after_seal_only': early,
                'readings': [[row[0], row[1], row[2]] for row in reading_rows(readings_r)],
            },
            {'seal': [list(seal_g3.exposures)]},
            {'seal': [list(seal_seen.exposures)]},
            ['station/bench/pages.py', 'station/capture/admit.py', 'station/capture/page_store.py'],
            'Declaration arriving before any member keeps the page pending until both members persist.',
        )

        # -- reclaim G1 fails after the device already dropped the page ----------
        faults.arm('source.after_reclaim')
        try:
            capture.acknowledgements.confirm_page(g1)
        except Exception:
            pass
        else:
            raise AssertionError('armed reclaim fault did not fire')
        g1_packages_gone = all(
            _missing(lambda eid=exposure_id: bench.deliver_reply(eid))
            for exposure_id in exposures_p.values()
        )
        g2 = bench.open_page(PAGE_SLOT)
        exposures_q, members_q = page_members(bench, g2, 'Q', PAGE_CODES['Q'])
        station.close()
        station = Station(page_path, checkpoint=faults)
        capture = station.attach(bench)
        confirmation_g1 = capture.acknowledgements.confirm_page(g1)
        g2_intact = (bench.deliver_reply(exposures_q['left']) == members_q['left']
                     and bench.deliver_reply(exposures_q['right']) == members_q['right'])
        try:
            capture.admit(PageReply(g1, bench.deliver_reply(exposures_q['left']).reply))
        except ValueError:
            misbound_rejected = True
        else:
            misbound_rejected = False
        emit(
            'page.reclaim_restart',
            {
                'device': PAGE_DEVICE, 'slot': PAGE_SLOT, 'fault': 'source.after_reclaim',
                'restart': 'station-reopen-device-online', 'reused_generation': g2.generation,
            },
            {
                'g1_packages_gone': True,
                'g1_confirmation_exposures': list(seal_g1.exposures),
                'g1_confirmation_slot': PAGE_SLOT,
                'g2_intact': True,
                'misbound_rejected': True,
            },
            {
                'g1_packages_gone': g1_packages_gone,
                'g1_confirmation_exposures': list(confirmation_g1.exposures),
                'g1_confirmation_slot': confirmation_g1.page.slot,
                'g2_intact': g2_intact,
                'misbound_rejected': misbound_rejected,
            },
            {'confirmation_exposures': [list(seal_g1.exposures)]},
            {'confirmation_exposures': [list(confirmation_g1.exposures)]},
            ['station/capture/page_acknowledge.py', 'station/capture/page_store.py',
             'station/bench/pages.py'],
            'Reclaim took effect before the lost acknowledgement; restart reuses the original page '
            'confirmation without touching the reused slot.',
        )

        capture.admit_seal(bench.seal_page(g2))
        for head in ('right', 'left'):
            capture.admit(members_q[head])
        result_q = station.quality.issue('day', 'Q')
        bench.close()

        # -- source stopped, station reopened, collect originals offline --------
        station.close()
        station = Station(page_path)
        offline_seal, offline_readings = station.collect_page(g1)
        original = station.lookup.collect(result_p.number)
        emit(
            'page.offline_collect',
            {
                'device': PAGE_DEVICE, 'slot': PAGE_SLOT, 'source': 'stopped',
                'collect_page_generation': g1.generation, 'result_number': result_p.number,
            },
            {
                'g1_exposures': list(seal_g1.exposures),
                'g1_readings': [[exposures_p['left'], 'left', 3],
                                [exposures_p['right'], 'right', 7]],
                'result_values': [10, 12, 2, True],
            },
            {
                'g1_exposures': list(offline_seal.exposures),
                'g1_readings': [[row[0], row[1], row[2]] for row in reading_rows(offline_readings)],
                'result_values': decision_row(original.result),
            },
            {
                'g1_receipts': [[exposures_p['left'], receipt_a.number],
                                [exposures_p['right'], receipt_b.number]],
                'result_sources': [[exposures_p['left'], 'left', 3],
                                   [exposures_p['right'], 'right', 7]],
            },
            {
                'g1_receipts': [[row[0], row[4]] for row in reading_rows(offline_readings)],
                'result_sources': [[row.exposure_id, row.head, row.raw_code]
                                   for row in original.sources],
            },
            ['station/capture/page_acknowledge.py', 'station/capture/page_store.py',
             'station/handover/lookup.py'],
            'The original page and quality evidence are re-read after the device goes offline and '
            'the station reopens.',
        )
    finally:
        try:
            bench.close()
        finally:
            station.close()


def _missing(call):
    try:
        call()
    except KeyError:
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description='Run a fixed-clamp shift and collect results offline.')
    parser.parse_args()
    with TemporaryDirectory(prefix='thickness-demo-') as directory:
        path = Path(directory) / 'station.sqlite'
        with Station(path) as station:
            station.shifts.open('day', ('P', 'Q', 'R'), (3, 1), (2, -2), 2)
            bench = OfflineBench(station.shifts, namespace='day')
            capture = station.attach(bench)
            issued = {}
            try:
                for _, specimen, codes, _, _ in SAMPLES:
                    capture_specimen(bench, capture, specimen, codes)
                    issued[specimen] = station.quality.issue('day', specimen)
            finally:
                bench.close()
        with Station(path) as station:
            sheet = station.handover.collect('day')
            if tuple(item.result.specimen_id for item in sheet.items) != ('P', 'Q'):
                raise AssertionError('handover differs from the expected roster projection')
            for case_id, specimen, codes, expected_values, expected_exposures in SAMPLES:
                result = issued[specimen]
                collected = station.lookup.collect(result.number)
                expected = list(expected_values)
                actual = decision_row(result)
                downstream_expected = {
                    'values': expected,
                    'sources': [[head, exposure, code] for head, exposure, code in zip(
                        ('left', 'right'), expected_exposures, codes, strict=True
                    )],
                }
                downstream_actual = {
                    'values': decision_row(collected.result),
                    'sources': [[row.head, row.exposure_id, row.raw_code] for row in collected.sources],
                }
                if actual != expected or downstream_actual != downstream_expected:
                    raise AssertionError(f'demo values or sources differ for {specimen!r}')
                print('POINTWISE_SCENARIO ' + json.dumps({
                    'case_id': case_id,
                    'input': {
                        'shift': 'day', 'specimen': specimen, 'left': [3, 1], 'right': [2, -2],
                        'tolerance_um': 2, 'raw_codes_left_right': codes,
                        'reply_order': ['right', 'left'],
                    },
                    'expected': expected,
                    'actual': actual,
                    'downstream_expected': downstream_expected,
                    'downstream_actual': downstream_actual,
                    'code_paths': [
                        'station/bench/events.py', 'station/capture/admit.py',
                        'station/quality/convert.py', 'station/quality/classify.py',
                        'station/quality/issue.py', 'station/handover/lookup.py',
                    ],
                    'oracle': 'Manually calculated affine values, signed difference, tolerance and exposure order.',
                }, sort_keys=True))
        run_page_demo(directory)


if __name__ == '__main__':
    main()
