import unittest

import kiwoom_minute_probe as K


def item(t, close="-100"):
    return {"cntr_tm": t, "open_pric": "+100", "high_pric": "+101", "low_pric": "-99", "cur_prc": close, "trde_qty": "5"}


def server(pages):
    calls = []

    def post(path, body, headers):
        calls.append((path, body, headers))
        data, hdr = pages[min(len(calls) - 1, len(pages) - 1)]
        return data, hdr
    return post, calls


def page(times, key=None):
    return ({"return_code": 0, K.LIST_KEY: [item(t) for t in times]},
            {"cont-yn": "Y", "next-key": key} if key else {"cont-yn": "N"})


def run(post, **kw):
    return K.fetch(post, "T", "005930", "1", sleep=lambda s: None, log=lambda *a: None, **kw)


class ProbeTest(unittest.TestCase):
    def test_pagination_and_sign(self):
        post, calls = server([page(["20261127090100"], "a"), page(["20261126090000"])])
        rows, info = run(post)
        self.assertEqual((len(rows), info["pages"], info["stop"]), (2, 2, "server_end"))
        self.assertEqual(rows["20261127090100"][4], 100.0)
        self.assertEqual(calls[1][2]["next-key"], "a")

    def test_not_advancing_stops(self):
        post, _ = server([page(["20261127090100"], "a"), page(["20261127090100"], "b")])
        self.assertEqual(run(post)[1]["stop"], "not_advancing")

    def test_repeated_key_stops(self):
        post, _ = server([page(["20261127090100"], "a"), page(["20261126090000"], "a")])
        self.assertEqual(run(post)[1]["stop"], "repeated_key")

    def test_three_errors_stop_and_keep_rows(self):
        post, calls = server([page(["20261127090100"], "a"), ({"return_code": 5, "return_msg": "x"}, {})])
        rows, info = run(post)
        self.assertEqual((len(rows), info["stop"], len(calls)), (1, "error", 4))

    def test_until(self):
        post, _ = server([page(["20251230090000"], "a")])
        self.assertEqual(run(post, until="20251231")[1]["stop"], "reached_until")

    def test_blank_and_bad_rows(self):
        self.assertIsNone(K.to_row(item("")))
        self.assertIsNone(K.to_row(item("20261127090100", close="500")))
        post, _ = server([page([""])])
        self.assertEqual(run(post)[1]["stop"], "no_rows")

    def test_only_quote_endpoints(self):
        with self.assertRaises(K.Stop):
            K.http_post("/api/dostk/ordr", {}, {"api-id": "kt10000"})
        with self.assertRaises(K.Stop):
            K.http_post(K.CHART_PATH, {}, {"api-id": "ka10081"})


if __name__ == "__main__":
    unittest.main()
