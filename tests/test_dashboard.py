import importlib
import json
import tempfile
import unittest
from pathlib import Path


def cbor_encode(value):
    def head(major, n):
        if n < 24:
            return bytes([(major << 5) | n])
        if n < 256:
            return bytes([(major << 5) | 24, n])
        if n < 65536:
            return bytes([(major << 5) | 25]) + n.to_bytes(2, "big")
        return bytes([(major << 5) | 26]) + n.to_bytes(4, "big")

    if value is None:
        return b"\xf6"
    if value is False:
        return b"\xf4"
    if value is True:
        return b"\xf5"
    if isinstance(value, int) and value >= 0:
        return head(0, value)
    if isinstance(value, int):
        return head(1, -1 - value)
    if isinstance(value, str):
        data = value.encode("utf-8")
        return head(3, len(data)) + data
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(cbor_encode(item) for item in value)
    if isinstance(value, dict):
        encoded = [cbor_encode(k) + cbor_encode(v) for k, v in value.items()]
        return head(5, len(encoded)) + b"".join(encoded)
    raise TypeError(value)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = importlib.import_module("scripts.dashboard")

    def test_get_metrics_reads_shared_host_files_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            shared = root / "data" / "metrics"
            logs.mkdir(parents=True)
            shared.mkdir(parents=True)

            entry = {
                "date": "2026-06-24",
                "script": "layer2_wiki",
                "timestamp": "2026-06-24T05:30:00",
                "hostname": "mini",
                "wiki_entries_written": 8,
            }
            (logs / "metrics.jsonl").write_text(json.dumps(entry) + "\n")
            (shared / "mini.jsonl").write_text(json.dumps(entry) + "\n")

            self.dashboard.METRICS_FILE = logs / "metrics.jsonl"
            self.dashboard.METRICS_SHARED_DIR = shared

            rows = self.dashboard.get_metrics(days=7)

            self.assertEqual(1, len(rows))
            self.assertEqual("mini", rows[0]["hostname"])

    def test_get_status_uses_job_status_matrix_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_file = Path(tmp) / "job_status.json"
            status_file.write_text(json.dumps({
                "mini": {
                    "layer2_wiki": {
                        "status": "success",
                        "last_success_at": "2026-06-24T05:30:00",
                        "last_finished_at": "2026-06-24T05:30:30",
                    }
                },
                "macbook": {
                    "dayflow_sync": {
                        "status": "failure",
                        "last_failure_at": "2026-06-24T11:00:00",
                        "last_error_summary": "timeout",
                    }
                },
            }))

            self.dashboard.JOB_STATUS_FILE = status_file
            self.dashboard.LAST_RUN_FILE = Path(tmp) / "missing_last_run.json"

            status = self.dashboard.get_status()

            self.assertIn("hosts", status)
            self.assertEqual("failure", status["hosts"]["macbook"]["dayflow_sync"]["status"])
            self.assertEqual("timeout", status["hosts"]["macbook"]["dayflow_sync"]["last_error_summary"])

    def test_get_status_keeps_local_last_run_when_shared_status_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_status = root / "data" / "job_status"
            logs = root / "logs"
            shared_status.mkdir(parents=True)
            logs.mkdir()
            (shared_status / "macbook.json").write_text(json.dumps({
                "dayflow_sync": {
                    "status": "success",
                    "last_success_at": "2026-06-24T14:21:30",
                }
            }))
            (logs / "last_run.json").write_text(json.dumps({
                "layer1_flomo": "2026-06-24T04:31:26"
            }))

            original_host = self.dashboard.LOCAL_HOSTNAME
            self.dashboard.LOCAL_HOSTNAME = "mini"
            self.dashboard.JOB_STATUS_SHARED_DIR = shared_status
            self.dashboard.JOB_STATUS_FILE = logs / "missing_job_status.json"
            self.dashboard.LAST_RUN_FILE = logs / "last_run.json"
            try:
                status = self.dashboard.get_status()
            finally:
                self.dashboard.LOCAL_HOSTNAME = original_host

            self.assertEqual("success", status["hosts"]["macbook"]["dayflow_sync"]["status"])
            self.assertEqual("success", status["hosts"]["mini"]["layer1_flomo"]["status"])
            self.assertEqual("2026-06-24T04:31:26", status["hosts"]["mini"]["layer1_flomo"]["last_success_at"])

    def test_status_color_uses_script_expected_interval(self):
        self.assertEqual("green", self.dashboard._script_status_color("memory_smoke_test", "success", 47.5))
        self.assertEqual("red", self.dashboard._script_status_color("dayflow_sync", "success", 47.5))
        self.assertEqual("red", self.dashboard._script_status_color("memory_smoke_test", "failure", 1))

    def test_browse_index_prefers_meili_documents_sorted_by_freshness(self):
        class Response:
            status_code = 200

            def json(self):
                return {
                    "results": [
                        {"title": "old wiki", "updated_at": "2026-06-20T08:00:00"},
                        {"title": "new wiki", "updated_at": "2026-06-24T09:00:00"},
                        {"title": "middle wiki", "created_at": "2026-06-22T09:00:00"},
                    ]
                }

        class FakeRequests:
            calls = []

            @classmethod
            def get(cls, url, **kwargs):
                cls.calls.append(url)
                return Response()

        original_http = self.dashboard.http_req
        self.dashboard.http_req = FakeRequests
        try:
            data = self.dashboard.browse_index("wiki_entries", "", 2)
        finally:
            self.dashboard.http_req = original_http

        self.assertTrue(data["ok"])
        self.assertEqual("meili", data["source"])
        self.assertEqual(["new wiki", "middle wiki"], [r["title"] for r in data["results"]])
        self.assertIn("/indexes/wiki_entries/documents", FakeRequests.calls[0])

    def test_browse_index_samples_enough_documents_before_sorting_by_freshness(self):
        docs = [
            {"title": f"old {i}", "updated_at": "2026-06-10T08:00:00"}
            for i in range(130)
        ]
        docs.append({"title": "newest after first page", "updated_at": "2026-06-24T09:00:00"})

        class Response:
            status_code = 200

            def json(self):
                return {"results": docs[: self.limit], "total": len(docs)}

        class FakeRequests:
            last_limit = None

            @classmethod
            def get(cls, url, **kwargs):
                cls.last_limit = kwargs["params"]["limit"]
                response = Response()
                response.limit = cls.last_limit
                return response

        original_http = self.dashboard.http_req
        self.dashboard.http_req = FakeRequests
        try:
            data = self.dashboard.browse_index("wiki_entries", "", 3)
        finally:
            self.dashboard.http_req = original_http

        self.assertGreaterEqual(FakeRequests.last_limit, 131)
        self.assertEqual("newest after first page", data["results"][0]["title"])

    def test_browse_index_supports_offset_and_has_more_after_sorting(self):
        docs = [
            {"title": f"doc {i}", "updated_at": f"2026-06-{24 - i:02d}T09:00:00"}
            for i in range(5)
        ]

        class Response:
            status_code = 200

            def json(self):
                return {"results": docs, "total": len(docs)}

        class FakeRequests:
            @staticmethod
            def get(url, **kwargs):
                return Response()

        original_http = self.dashboard.http_req
        self.dashboard.http_req = FakeRequests
        try:
            data = self.dashboard.browse_index("wiki_entries", "", 2, offset=2)
        finally:
            self.dashboard.http_req = original_http

        self.assertEqual(["doc 2", "doc 3"], [r["title"] for r in data["results"]])
        self.assertTrue(data["has_more"])
        self.assertEqual(4, data["next_offset"])

    def test_browse_index_returns_human_readable_index_meta(self):
        class Response:
            status_code = 200

            def json(self):
                return {"results": [
                    {"source": "flomo"},
                    {"source": "dayflow"},
                    {"source": "flomo"},
                ], "total": 3}

        class FakeRequests:
            @staticmethod
            def get(url, **kwargs):
                return Response()

        original_http = self.dashboard.http_req
        self.dashboard.http_req = FakeRequests
        try:
            data = self.dashboard.browse_index("memory_chunks", "", 5)
        finally:
            self.dashboard.http_req = original_http

        self.assertEqual("事实碎片", data["meta"]["title"])
        self.assertIn("Dayflow", data["meta"]["sources"])
        self.assertIn("flomo", data["meta"]["sources"])
        self.assertEqual("flomo", data["source_summary"][0]["source"])
        self.assertEqual(2, data["source_summary"][0]["count"])

    def test_browse_index_falls_back_to_memory_search_when_meili_unavailable(self):
        class Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class FakeRequests:
            @staticmethod
            def get(url, **kwargs):
                if "/indexes/" in url:
                    return Response(503, {})
                return Response(200, {
                    "total": 1,
                    "results": [{"text": "fallback result"}],
                })

        original_http = self.dashboard.http_req
        self.dashboard.http_req = FakeRequests
        try:
            data = self.dashboard.browse_index("wiki_entries", "", 5)
        finally:
            self.dashboard.http_req = original_http

        self.assertTrue(data["ok"])
        self.assertEqual("memory-api", data["source"])
        self.assertEqual([{"text": "fallback result"}], data["results"])

    def test_anda_browse_returns_overview_without_raw_conversations(self):
        class Response:
            def __init__(self, payload):
                self.status_code = 200
                self._payload = payload

            def json(self):
                return self._payload

        class FakeRequests:
            @staticmethod
            def get(url, **kwargs):
                if url.endswith("/info"):
                    return Response({"result": {
                        "concepts": 220,
                        "propositions": 530,
                        "conversations": 1699,
                        "formation_usage": {"input_tokens": 1000000, "requests": 10},
                        "recall_usage": {"input_tokens": 200000, "requests": 3},
                    }})
                return Response({"result": [{
                    "_id": 1,
                    "label": "formation",
                    "created_at": 1782252010387,
                    "messages": [{"content": [{"text": "{\"raw\":\"large\"}"}]}],
                }]})

        original_http = self.dashboard.http_req
        self.dashboard.http_req = FakeRequests
        try:
            data = self.dashboard.browse_index("anda", "", 5)
        finally:
            self.dashboard.http_req = original_http

        self.assertEqual("anda-info", data["source"])
        self.assertIn("overview_cards", data["results"])
        self.assertNotIn("recent_conversations", data["results"])
        self.assertNotIn("messages", json.dumps(data["results"], ensure_ascii=False))

    def test_anda_graph_browse_reads_processed_concepts_and_propositions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            space = root / "data" / "anda_main"
            concept_dir = space / "concepts" / "data"
            proposition_dir = space / "propositions" / "data"
            concept_dir.mkdir(parents=True)
            proposition_dir.mkdir(parents=True)

            (concept_dir / "1.cbor").write_bytes(cbor_encode({
                "f": {
                    0: 1,
                    1: "Project",
                    2: "myScope Dashboard",
                    3: {"description": "用于监控记忆系统运行情况。"},
                    4: {"source": "brain:conversation:test", "observed_at": "2026-06-24T08:00:00+08:00"},
                }
            }))
            (concept_dir / "2.cbor").write_bytes(cbor_encode({
                "f": {
                    0: 2,
                    1: "Domain",
                    2: "个人记忆系统",
                    3: {"description": "长期知识管理和记忆召回。"},
                    4: {"source": "brain:conversation:test", "observed_at": "2026-06-24T07:00:00+08:00"},
                }
            }))
            (proposition_dir / "1.cbor").write_bytes(cbor_encode({
                "f": {
                    0: 1,
                    1: "C:1",
                    2: "C:2",
                    3: ["belongs_to_domain"],
                    4: {
                        "belongs_to_domain": {
                            "m": {
                                "source": "brain:conversation:test",
                                "confidence": "0.95",
                                "observed_at": "2026-06-24T08:10:00+08:00",
                            }
                        }
                    },
                }
            }))

            original_dir = self.dashboard.ANDA_DATA_DIR
            original_space = self.dashboard.ANDA_SPACE_ID
            self.dashboard.ANDA_DATA_DIR = root
            self.dashboard.ANDA_SPACE_ID = "anda_main"
            try:
                concepts = self.dashboard.browse_index("anda_concepts", "", 5)
                propositions = self.dashboard.browse_index("anda_propositions", "", 5)
            finally:
                self.dashboard.ANDA_DATA_DIR = original_dir
                self.dashboard.ANDA_SPACE_ID = original_space

        self.assertEqual("anda-cbor", concepts["source"])
        self.assertEqual("myScope Dashboard", concepts["results"][0]["title"])
        self.assertIn("监控记忆系统", concepts["results"][0]["content"])
        self.assertEqual("anda-cbor", propositions["source"])
        self.assertIn("myScope Dashboard", propositions["results"][0]["title"])
        self.assertIn("属于领域", propositions["results"][0]["title"])
        self.assertNotIn("recent_batches", json.dumps(propositions, ensure_ascii=False))

    def test_dashboard_page_counts_layer3_index_documents_as_articles(self):
        html = Path("scripts/dashboard_page.html").read_text()

        self.assertIn("'documents_indexed'", html)
        self.assertIn("byDate[d].layer3_index?.documents_indexed", html)
        self.assertIn("byDate[d].layer1_flomo?.chunks", html)

    def test_dashboard_page_uses_infinite_modal_loading_and_right_axis_articles(self):
        html = Path("scripts/dashboard_page.html").read_text()

        self.assertIn("next_offset", html)
        self.assertIn("loadMoreBrowseData", html)
        self.assertIn("yArticles", html)
        self.assertIn("第三层文章（右轴）", html)

    def test_dashboard_page_opens_graph_details_and_uses_compact_source_chips(self):
        html = Path("scripts/dashboard_page.html").read_text()

        self.assertIn("openBrowse('anda_concepts', '图谱概念')", html)
        self.assertIn("openBrowse('anda_propositions', '图谱命题')", html)
        self.assertIn("source-chip", html)
        self.assertIn("source-name", html)


if __name__ == "__main__":
    unittest.main()
