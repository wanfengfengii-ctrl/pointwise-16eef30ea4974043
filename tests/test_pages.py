from dataclasses import replace

from station.bench.confirmation import PageConfirmation
from station.bench.recording import PageKey, PageReply, PageSeal
from station.setup.database import InjectedFailure
from tests import StationCase


class PageCase(StationCase):
    device = 'test'

    def setUp(self):
        super().setUp()
        self.open_shift()

    def page(self, slot=4):
        return self.bench.open_page(slot)

    def prepare_page(self, page, specimen='P', codes=(3, 7), shift='A'):
        self.mount(specimen, shift)
        exposures = {head: self.bench.request_measurement(head) for head in ('left', 'right')}
        members = []
        for (head, exposure_id), code in zip(exposures.items(), codes, strict=True):
            self.edge('open', exposure_id, head)
            self.edge('close', exposure_id, head)
            members.append(self.bench.record(exposure_id, code, page=page))
        self.bench.emit('unmount')
        return exposures, tuple(members)

    def seal(self, page):
        return self.bench.seal_page(page)

    def admit_page(self, page_replies):
        for page_reply in page_replies:
            self.capture.admit(page_reply)

    def raw_reading(self, exposure_id, *, station=None):
        return self.reading(exposure_id, station=station)


class TestPageObjects(PageCase):
    def test_page_key_fields(self):
        page = PageKey('dev', 4, 2)
        self.assertEqual(page, PageKey('dev', 4, 2))
        self.assertNotEqual(page, PageKey('dev', 4, 1))
        self.assertNotEqual(page, PageKey('dev', 5, 2))
        for bad in ((None, 4, 1), ('dev', -1, 1), ('dev', 4, 0), ('dev', True, 1),
                    ('', 4, 1), ('dev', 4, 1.0)):
            with self.subTest(args=bad), self.assertRaises(ValueError):
                PageKey(*bad)

    def test_seal_and_confirmation_validation(self):
        page = PageKey('dev', 4, 1)
        for exposures in ((), ('a', 'a'), ['a'], 'a'):
            with self.subTest(exposures=exposures), self.assertRaises(ValueError):
                PageSeal(page, exposures)
        with self.assertRaises(ValueError):
            PageConfirmation(page, ())

    def test_open_generation(self):
        page = self.page(4)
        self.assertEqual(page, PageKey(self.device, 4, 1))
        with self.assertRaises(ValueError):
            self.bench.open_page(4)
        for bad in (-1, True, 1.0):
            with self.subTest(slot=bad), self.assertRaises(ValueError):
                self.bench.open_page(bad)
        self.assertEqual(self.bench.open_page(5), PageKey(self.device, 5, 1))


class TestPageRecording(PageCase):
    def test_record_repeat_and_conflict(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        self.assertEqual(tuple(member.reply.exposure_id for member in members),
                         (exposures['left'], exposures['right']))
        self.assertTrue(all(member.page == page for member in members))
        self.assertEqual(self.bench.deliver_reply(exposures['right']), members[1])
        self.assertEqual(self.bench.record(exposures['right'], 7, page=page), members[1])
        with self.assertRaises(ValueError):
            self.bench.record(exposures['right'], 8, page=page)
        other = self.bench.open_page(5)
        with self.assertRaises(ValueError):
            self.bench.record(exposures['right'], 7, page=other)
        with self.assertRaises(ValueError):
            self.bench.record(exposures['right'], 7)

    def test_seal_freezes_members(self):
        page = self.page()
        exposures, _ = self.prepare_page(page)
        seal = self.seal(page)
        self.assertEqual(seal, PageSeal(page, (exposures['left'], exposures['right'])))
        self.assertEqual(self.seal(page), seal)
        self.assertEqual(self.bench.deliver_seal(page), seal)

    def test_empty_seal_rejected(self):
        page = self.page()
        with self.assertRaises(ValueError):
            self.seal(page)
        with self.assertRaises(ValueError):
            self.bench.deliver_seal(page)

    def test_unknown_page_seal_keyerror(self):
        with self.assertRaises(KeyError):
            self.bench.deliver_seal(PageKey(self.device, 9, 1))
        with self.assertRaises(KeyError):
            self.seal(PageKey(self.device, 9, 1))

    def test_add_to_sealed_page_rejected(self):
        page = self.page()
        self.prepare_page(page)
        self.seal(page)
        self.mount('Q')
        exposure = self.bench.request_measurement('left')
        right = self.bench.request_measurement('right')
        self.edge('open', exposure, 'left')
        self.edge('close', exposure, 'left')
        self.edge('open', right, 'right')
        self.edge('close', right, 'right')
        with self.assertRaises(ValueError):
            self.bench.record(exposure, 4, page=page)
        self.bench.emit('unmount')

    def test_single_confirm_rejected(self):
        page = self.page()
        exposures, _ = self.prepare_page(page)
        self.seal(page)
        with self.assertRaises(ValueError):
            self.bench.confirm(exposures['left'])
        self.assertIsNotNone(self.bench.deliver_reply(exposures['left']))

    def test_plain_record_unaffected(self):
        exposures = self.prepare()
        self.assertFalse(isinstance(self.bench.deliver_reply(exposures['left']), PageReply))
        self.assertEqual(self.capture.admit(self.bench.deliver_reply(exposures['left'])).exposure_id,
                         exposures['left'])


class TestPageAdmission(PageCase):
    def test_out_of_order_replies(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        seal = self.seal(page)
        with self.assertRaises(KeyError):
            self.station.collect_page(page)
        receipt = self.capture.admit(members[1])
        self.assertEqual(receipt.exposure_id, exposures['right'])
        self.assertEqual(self.capture.admit(members[1]), receipt)
        self.assertEqual(self.capture.admit_seal(seal), seal)
        self.assertIsNone(self.station.collect_page(page))
        self.capture.admit(members[0])
        seal_out, readings = self.station.collect_page(page)
        self.assertEqual(seal_out, seal)
        self.assertEqual(tuple((r.identity.exposure_id, r.identity.head, r.raw_code) for r in readings),
                         ((exposures['left'], 'left', 3), (exposures['right'], 'right', 7)))
        self.assertEqual(tuple(r.receipt.exposure_id for r in readings), seal.exposures)

    def test_seal_before_replies(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        seal = self.seal(page)
        self.capture.admit_seal(seal)
        self.assertIsNone(self.station.collect_page(page))
        self.admit_page(reversed(members))
        collected_seal, readings = self.station.collect_page(page)
        self.assertEqual(collected_seal, seal)
        self.assertEqual(tuple(r.identity.exposure_id for r in readings), seal.exposures)

    def test_wrong_device_page(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        forged = PageReply(PageKey('other', 4, 1), members[0].reply)
        with self.assertRaises(ValueError):
            self.capture.admit(forged)

    def test_wrong_member_rejected(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        self.capture.admit_seal(self.seal(page))
        foreign = PageSeal(page, (exposures['left'],))
        with self.assertRaises(ValueError):
            self.capture.admit_seal(foreign)
        tampered = PageReply(members[0].page, replace(members[0].reply, raw_code=9))
        with self.assertRaises(ValueError):
            self.capture.admit(tampered)
        self.assertIsNone(self.station.collect_page(page))

    def test_strip_page_identity_rejected(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        with self.assertRaises(ValueError):
            self.capture.admit(members[0].reply)
        with self.assertRaises(KeyError):
            self.station.collect_page(page)
        self.assertIsNone(self.reading(exposures['left']))

    def test_unknown_page_keyerror(self):
        with self.assertRaises(KeyError):
            self.station.collect_page(PageKey(self.device, 9, 1))
        with self.assertRaises(KeyError):
            self.capture.acknowledgements.confirm_page(PageKey(self.device, 9, 1))

    def test_unbound_collect(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        seal = self.seal(page)
        self.admit_page(members)
        self.capture.admit_seal(seal)
        opened = self.open_connection()
        self.assertEqual(opened.collect_page(page)[0], seal)

    def test_readiness_before_confirm(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        seal = self.seal(page)
        self.capture.admit_seal(seal)
        self.assertIsNone(self.capture.acknowledgements.confirm_page(page))
        self.admit_page(members)
        confirmation = self.capture.acknowledgements.confirm_page(page)
        self.assertEqual(confirmation, PageConfirmation(page, seal.exposures))

    def test_confirm_requires_all_inputs(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        self.capture.admit(members[0])
        self.assertIsNone(self.capture.acknowledgements.confirm_page(page))
        self.capture.admit_seal(self.seal(page))
        self.assertIsNone(self.capture.acknowledgements.confirm_page(page))
        self.capture.admit(members[1])
        self.assertIsNotNone(self.capture.acknowledgements.confirm_page(page))

    def test_page_admit_failure_keeps_source(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        self.faults.arm('admit.before_commit')
        with self.assertRaisesRegex(InjectedFailure, 'admit.before_commit'):
            self.capture.admit(members[0])
        with self.assertRaises(KeyError):
            self.station.collect_page(page)
        self.assertEqual(self.bench.deliver_reply(exposures['left']), members[0])
        self.capture.admit(members[0])
        self.capture.admit_seal(self.seal(page))
        self.capture.admit(members[1])
        self.assertEqual(self.station.collect_page(page)[1][0].raw_code, 3)

    def test_participates_in_quality(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        self.admit_page(members)
        result = self.station.quality.issue('A', 'P')
        self.assertEqual((result.left_um, result.right_um, result.difference_um, result.accepted),
                         (10, 12, 2, True))


class TestPageReclaim(PageCase):
    def fully_admit_page(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        seal = self.seal(page)
        self.admit_page(members)
        self.capture.admit_seal(seal)
        return page, exposures, members, seal

    def test_reclaim_clears_page(self):
        page, exposures, members, seal = self.fully_admit_page()
        confirmation = self.capture.acknowledgements.confirm_page(page)
        self.assertEqual(confirmation, PageConfirmation(page, seal.exposures))
        for exposure in exposures.values():
            with self.assertRaises(KeyError):
                self.bench.deliver_reply(exposure)
            self.assertIsNone(self.bench.exposure(exposure))
        self.assertEqual(self.capture.acknowledgements.confirm_page(page), confirmation)

    def test_reuse_slot_after_reclaim(self):
        page, exposures, members, seal = self.fully_admit_page()
        confirmation = self.capture.acknowledgements.confirm_page(page)
        next_page = self.bench.open_page(4)
        self.assertEqual(next_page, PageKey(self.device, 4, 2))
        q_exposures, q_members = self.prepare_page(next_page, specimen='Q', codes=(4, 6))
        self.assertEqual(self.capture.acknowledgements.confirm_page(page), confirmation)
        self.admit_page(q_members)
        self.assertEqual(self.station.collect_page(page)[1][0].raw_code, 3)
        self.assertIsNone(self.station.collect_page(next_page))

    def test_after_reclaim_failure_and_restart(self):
        page, exposures, members, seal = self.fully_admit_page()
        self.faults.arm('source.after_reclaim')
        with self.assertRaisesRegex(InjectedFailure, 'source.after_reclaim'):
            self.capture.acknowledgements.confirm_page(page)
        for exposure in exposures.values():
            with self.assertRaises(KeyError):
                self.bench.deliver_reply(exposure)
        next_page = self.bench.open_page(4)
        q_exposures, q_members = self.prepare_page(next_page, specimen='Q', codes=(4, 6))
        self.admit_page(q_members)
        self.station.close()
        restarted = self.open_connection()
        recapture = restarted.attach(self.bench)
        confirmation = recapture.acknowledgements.confirm_page(page)
        self.assertEqual(confirmation, PageConfirmation(page, seal.exposures))
        self.assertIsNone(restarted.collect_page(next_page))
        self.assertEqual(self.bench.deliver_reply(q_exposures['left']).reply.raw_code, 4)
        with self.assertRaises(ValueError):
            recapture.admit(PageReply(page, self.bench.deliver_reply(q_exposures['left']).reply))

    def test_before_confirm_failure_keeps_page(self):
        page, exposures, members, seal = self.fully_admit_page()
        self.faults.arm('source.before_confirm')
        with self.assertRaisesRegex(InjectedFailure, 'source.before_confirm'):
            self.capture.acknowledgements.confirm_page(page)
        for exposure_id in exposures.values():
            self.assertEqual(self.bench.deliver_reply(exposure_id).reply.exposure_id, exposure_id)
        self.assertIsNotNone(self.capture.acknowledgements.confirm_page(page))

    def test_collect_after_reclaim_and_reopen(self):
        page, exposures, members, seal = self.fully_admit_page()
        self.capture.acknowledgements.confirm_page(page)
        self.station.close()
        restarted = self.open_connection()
        collected_seal, readings = restarted.collect_page(page)
        self.assertEqual(collected_seal, seal)
        self.assertEqual(tuple((r.identity.exposure_id, r.identity.head, r.raw_code) for r in readings),
                         ((exposures['left'], 'left', 3), (exposures['right'], 'right', 7)))
        result = restarted.quality.issue('A', 'P')
        self.assertEqual(result.difference_um, 2)

    def test_partial_handoff_across_restart(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        self.capture.admit(members[0])
        self.capture.admit_seal(self.seal(page))
        self.station.close()
        restarted = self.open_connection()
        recapture = restarted.attach(self.bench)
        self.assertIsNone(recapture.acknowledgements.confirm_page(page))
        self.assertIsNone(restarted.collect_page(page))
        recapture.admit(members[1])
        self.assertIsNotNone(recapture.acknowledgements.confirm_page(page))


class TestPageReplayAndEdges(PageCase):
    def test_replay_after_reclaim(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        seal = self.seal(page)
        self.admit_page(members)
        self.capture.admit_seal(seal)
        confirmation = self.capture.acknowledgements.confirm_page(page)
        # Same page arrivals can still be replayed from persisted records.
        for member in members:
            self.assertEqual(self.capture.admit(member).exposure_id, member.reply.exposure_id)
        self.assertEqual(self.capture.admit_seal(seal), seal)
        self.assertEqual(self.capture.acknowledgements.confirm_page(page), confirmation)
        self.assertEqual(self.bench.deliver_seal(page), seal)

    def test_generation_increments_only_after_reclaim(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        self.seal(page)
        with self.assertRaises(ValueError):
            self.bench.open_page(4)
        self.admit_page(members)
        self.capture.admit_seal(self.seal(page))
        self.capture.acknowledgements.confirm_page(page)
        self.assertEqual(self.bench.open_page(4), PageKey(self.device, 4, 2))

    def test_seal_extra_member_already_admitted_rejected(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        self.capture.admit(members[0])
        with self.assertRaises(ValueError):
            # Declaration omits an already admitted member.
            self.capture.admit_seal(PageSeal(page, (exposures['right'],)))
        self.assertIsNone(self.station.collect_page(page))
        self.capture.admit_seal(self.seal(page))
        self.capture.admit(members[1])
        self.assertIsNotNone(self.station.collect_page(page))

    def test_retry_pending_ignores_page_members(self):
        page = self.page()
        exposures, members = self.prepare_page(page)
        self.admit_page(members)
        self.assertEqual(self.pending(), ())
        self.assertEqual(self.capture.acknowledgements.retry_pending(), ())
        # Single-exposure confirmation of page material is refused outright.
        self.assertFalse(self.capture.acknowledgements.confirm_exposure(exposures['left']))
        with self.assertRaises(ValueError):
            self.bench.confirm(exposures['left'])

    def test_mixed_plain_and_page(self):
        plain = self.prepare('Q', codes=(4, 6))
        self.capture.admit(self.bench.deliver_reply(plain['left']))
        page = self.page()
        exposures, members = self.prepare_page(page, specimen='P')
        self.admit_page(members)
        self.capture.admit_seal(self.seal(page))
        self.assertEqual((self.station.quality.issue('A', 'P').difference_um,
                          self.station.quality.issue('A', 'Q').missing), (2, ('right',)))
