"""Offline behavioral checks; no personal credentials or live API calls."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

SOURCE = Path(__file__).resolve().parents[1] / "notebook-feishu-onboarding/scripts/notehelper_client.py"
spec = importlib.util.spec_from_file_location("client", SOURCE)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class ClientTests(unittest.TestCase):
    def run_main(self, argv):
        stream = io.StringIO()
        with patch.object(client.sys, "argv", ["client"] + argv), contextlib.redirect_stdout(stream):
            client.main()
        return json.loads(stream.getvalue())

    def test_doctor_does_not_read_key_or_call_network(self):
        with patch.object(client, "load_key", side_effect=AssertionError), patch.object(
                client, "request", side_effect=AssertionError):
            result = self.run_main(["doctor"])
        self.assertFalse(result["remote_verified"])

    def test_missing_key_never_falls_back_to_other_users(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(client.ClientError):
                client.load_key(Path(folder) / "missing.json")

    def test_configure_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "config.json"
            target.write_text('{"api_key":"existing-test-only"}')
            before = target.read_bytes()
            with self.assertRaises(client.ClientError):
                client.configure(target, False)
            self.assertEqual(target.read_bytes(), before)

    def test_hidden_input_saved_without_echo(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "new/config.json"
            stream = io.StringIO()
            with patch.object(client.sys.stdin, "isatty", return_value=True), patch.object(
                    client.getpass, "getpass", return_value="offline-test-key"), contextlib.redirect_stdout(stream):
                client.configure(target, False)
            self.assertNotIn("offline-test-key", stream.getvalue())
            self.assertEqual(json.loads(target.read_text())["api_key"], "offline-test-key")
            if os.name != "nt":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_agent_stdin_saves_chat_provided_key_without_tty_or_echo(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "config.json"
            with patch.object(client.sys, "stdin", io.StringIO("simulated-chat-key\n")), patch.object(
                    client.getpass, "getpass", side_effect=AssertionError):
                result = self.run_main(["--config", str(target), "configure", "--stdin"])
            self.assertTrue(result["configured"])
            self.assertNotIn("simulated-chat-key", json.dumps(result))
            self.assertEqual(json.loads(target.read_text())["api_key"], "simulated-chat-key")

    def test_empty_stdin_never_creates_config(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "config.json"
            with patch.object(client.sys, "stdin", io.StringIO("")):
                with self.assertRaises(client.ClientError):
                    client.configure(target, False, from_stdin=True)
            self.assertFalse(target.exists())

    def test_stdin_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "config.json"
            target.write_text('{"api_key":"existing-test-only"}')
            before = target.read_bytes()
            with patch.object(client.sys, "stdin", io.StringIO("replacement-test-only\n")):
                with self.assertRaises(client.ClientError):
                    client.configure(target, False, from_stdin=True)
            self.assertEqual(target.read_bytes(), before)

    def test_stdin_mode_refuses_echoing_terminal(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
                client.sys.stdin, "isatty", return_value=True):
            with self.assertRaises(client.ClientError):
                client.configure(Path(folder) / "config.json", False, from_stdin=True)

    def test_workspace_is_unique_and_outside_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "user-work"
            paths = []
            for _ in range(2):
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    client.prepare_workspace(root)
                result = json.loads(stream.getvalue())
                paths.append(Path(result["workspace"]))
                self.assertTrue(paths[-1].is_dir())
                self.assertEqual(paths[-1].parent, root.resolve())
            self.assertNotEqual(paths[0], paths[1])

    def test_private_export_into_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / "skill"
            (package / "scripts").mkdir(parents=True)
            target = package / "selected-notes.json"
            with patch.object(client, "__file__", str(package / "scripts/client.py")):
                with self.assertRaises(client.ClientError):
                    client.output({"content": "simulated private content"}, str(target))
            self.assertFalse(target.exists())

    def test_zero_articles_is_valid_auth(self):
        with patch.object(client, "load_key", return_value="test"), patch.object(
                client, "request", return_value={"count": 0}):
            result = self.run_main(["check"])
        self.assertTrue(result["key_verified"])
        self.assertEqual(result["article_count"], 0)

    def test_unknown_auth_response_does_not_pass(self):
        with patch.object(client, "load_key", return_value="test"), patch.object(
                client, "request", return_value={"message": "ok"}):
            with self.assertRaises(client.ClientError):
                self.run_main(["check"])

    def test_search_preserves_full_content_and_uses_variables(self):
        body = "# 标题\n\n开头\n" + "中段内容\n" * 400 + "\n结尾"
        result = {"edges": [{"node": {"id": "test-id", "title": "测试", "content": body}}],
                  "pageInfo": {"hasNextPage": True}}
        with patch.object(client, "request", return_value=result) as call:
            value = client.search("test", "选定标题", 1, 10, True, "test-id")
        self.assertEqual(value["articles"][0]["content"], body)
        self.assertIsNone(value["next_after"])
        self.assertEqual(call.call_args.args[2]["variables"],
                         {"query": "选定标题", "first": 1, "after": 10})

    def test_metadata_exposes_position_without_other_body(self):
        nodes = [{"node": {"id": "A", "content": "not requested"}},
                 {"node": {"id": "B", "content": "not requested"}}]
        with patch.object(client, "request", return_value={"edges": nodes}):
            value = client.search("test", "same title", 10, 20, False)
        self.assertEqual([n["result_after"] for n in value["articles"]], [20, 21])
        self.assertTrue(all("content" not in n for n in value["articles"]))

    def test_selected_second_note_exports_only_selected_body(self):
        def reply(path, key, payload):
            nodes = [{"node": {"id": "A", "content": "body A"}},
                     {"node": {"id": "B", "content": "body B"}}]
            variables = payload["variables"]
            page = nodes[variables["after"]:variables["after"] + variables["first"]]
            return {"edges": page, "pageInfo": {"hasNextPage": False}}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "selected.json"
            with patch.object(client, "load_key", return_value="dummy"), patch.object(
                    client, "request", side_effect=reply) as call:
                self.run_main(["search", "--query", "same title", "--content",
                               "--id", "B", "--limit", "1", "--after", "1",
                               "--output", str(target)])
            value = json.loads(target.read_text())
        self.assertEqual([n["id"] for n in value["articles"]], ["B"])
        self.assertEqual(value["articles"][0]["content"], "body B")
        self.assertNotIn("body A", json.dumps(value))
        self.assertEqual(call.call_args.args[2]["variables"],
                         {"query": "same title", "first": 1, "after": 1})

    def test_content_requires_id_and_single_result_before_network(self):
        for note_id, limit in [(None, 1), ("A", 10)]:
            with patch.object(client, "request") as call:
                with self.assertRaises(client.ClientError):
                    client.search("dummy", "same title", limit, 0, True, note_id)
                call.assert_not_called()

    def test_bulk_wrong_or_missing_body_never_exports(self):
        a = {"node": {"id": "A", "content": "private A"}}
        b = {"node": {"id": "B", "content": "private B"}}
        for edges in [[a, b], [b], []]:
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "selected.json"
                stream = io.StringIO()
                with patch.object(client, "load_key", return_value="dummy"), patch.object(
                        client, "request", return_value={"edges": edges}), contextlib.redirect_stdout(stream):
                    with self.assertRaises(client.ClientError):
                        self.run_main(["search", "--query", "same title", "--content",
                                       "--id", "A", "--limit", "1", "--output", str(target)])
                self.assertFalse(target.exists())
                self.assertNotIn("private", stream.getvalue())

    def test_standard_graphql_shape_and_metadata_only(self):
        with patch.object(client, "request", return_value={"data": {"search": {
                "edges": [{"node": {"id": "test-id", "content": "private"}}],
                "pageInfo": {"hasNextPage": False}}}}):
            value = client.search("test", "", 10, 0, False)
        self.assertNotIn("content", value["articles"][0])
        self.assertIsNone(value["next_after"])

    def test_malformed_search_does_not_look_like_empty_library(self):
        with patch.object(client, "request", return_value={"data": {"wrong": []}}):
            with self.assertRaises(client.ClientError):
                client.search("test", "", 10, 0, False)

    def test_submit_uses_nested_task_and_only_one_request(self):
        with patch.object(client, "load_key", return_value="test"), patch.object(
                client, "request", return_value={"data": {"tasks": [{"task_id": "test-task"}]}}) as call:
            value = self.run_main(["submit", "https://example.org/article"])
        self.assertEqual(value["task_id"], "test-task")
        self.assertNotIn("feishu_written", value)
        self.assertEqual(call.call_count, 1)

    def test_cloud_success_is_not_feishu_success(self):
        for shape in ({"data": {"status": "success", "note_id": "test-note"}},
                      {"data": {"tasks": [{"status": "success", "note_id": "test-note"}]}}):
            with patch.object(client, "load_key", return_value="test"), patch.object(
                    client, "request", return_value=shape):
                value = self.run_main(["progress", "test-task"])
            self.assertFalse(value["feishu_written"])
            self.assertEqual(value["note_id"], "test-note")

    def test_request_sets_explicit_ua_and_auth_header(self):
        opener = unittest.mock.Mock()
        opener.open.return_value.__enter__ = lambda _: io.BytesIO(b'{"count":0}')
        opener.open.return_value.__exit__ = lambda *args: False
        with patch.object(client, "build_opener", return_value=opener):
            client.request("/api/stats/article-count", "test-only-key")
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_header("User-agent"), client.UA)
        self.assertEqual(req.get_header("X-api-key"), "test-only-key")
        self.assertEqual(req.full_url, client.BASE + "/api/stats/article-count")

    def test_http_errors_never_echo_body_or_key(self):
        err = HTTPError(client.BASE, 402, "private-test-key", {}, io.BytesIO(b"private-test-key"))
        with patch.object(client, "build_opener") as factory:
            factory.return_value.open.side_effect = err
            with self.assertRaises(client.ClientError) as caught:
                client.request("/note/save", "private-test-key", {"link_url": "https://example.org"})
        self.assertIn("402", str(caught.exception))
        self.assertNotIn("private-test-key", str(caught.exception))
        self.assertEqual(factory.return_value.open.call_count, 1)

    def test_redirect_does_not_forward_credentials(self):
        with self.assertRaises(client.ClientError):
            client.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")

    def test_export_never_overwrites_existing_user_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "notes.json"
            target.write_text("existing")
            with self.assertRaises(FileExistsError):
                client.output({"private": "new"}, str(target))
            self.assertEqual(target.read_text(), "existing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
