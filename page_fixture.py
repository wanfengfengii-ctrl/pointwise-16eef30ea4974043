import json

from station import Station
from station.bench.offline import OfflineBench


SHIFT = "day"
DEVICE = "page-demo"
SLOT = 4
HEADS = ("left", "right")
CODES = {"P": (3, 7), "Q": (4, 6)}


def open_sample_shift(station):
    return station.shifts.open(SHIFT, tuple(CODES), (3, 1), (2, -2), 2)


def prepare_exposures(bench, specimen):
    bench.emit("mount", shift_id=SHIFT, specimen_id=specimen)
    exposures = []
    for head in HEADS:
        exposure_id = bench.request_measurement(head)
        bench.emit("open", exposure_id=exposure_id, head=head)
        bench.emit("close", exposure_id=exposure_id, head=head)
        exposures.append(exposure_id)
    bench.emit("unmount")
    return tuple(exposures)


def main():
    with Station(":memory:") as station:
        open_sample_shift(station)
        bench = OfflineBench(station.shifts, namespace=DEVICE)
        capture = station.attach(bench)
        results = []
        try:
            for specimen, codes in CODES.items():
                exposures = prepare_exposures(bench, specimen)
                for exposure_id, code in zip(exposures, codes, strict=True):
                    bench.record(exposure_id, code)
                for exposure_id in reversed(exposures):
                    capture.admit(bench.deliver_reply(exposure_id))
                results.append(station.quality.issue(SHIFT, specimen))
        finally:
            bench.close()
        output = []
        for result, expected in zip(results, ((10, 12, 2, True), (13, 10, -3, False)), strict=True):
            item = station.lookup.collect(result.number)
            values = (item.result.left_um, item.result.right_um, item.result.difference_um, item.result.accepted)
            sources = [(row.exposure_id, row.head, row.raw_code) for row in item.sources]
            if values != expected or tuple(source[2] for source in sources) != CODES[result.specimen_id]:
                raise AssertionError(f"Unexpected V1 values or sources for {result.specimen_id}")
            output.append({"specimen": result.specimen_id, "values": values, "sources": sources})
        print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
