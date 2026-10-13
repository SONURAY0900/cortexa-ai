"""
Guest chat (no account) and claiming guest chats after login.

Supabase, Groq and token verification are mocked; no network.
"""

import json
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from auth import supabase_auth
from auth.supabase_auth import Guest, get_current_user, get_user_or_guest, is_guest
from config import settings
from services import guest_limits
from tests.test_security import make_user


class Query:
    """Records a supabase-py query chain and returns canned data."""

    def __init__(self, log, table, data=None):
        self.log, self.table, self.data, self.calls = log, table, data, []
        log.append(self)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
            return self
        return call

    def execute(self):
        self.calls.append(("execute", ()))
        return SimpleNamespace(data=self.data if self.data is not None else [], count=None)


class FakeSupabase:
    def __init__(self, data_for=None):
        self.queries = []
        self.data_for = data_for or {}

    def table(self, name):
        return Query(self.queries, name, self.data_for.get(name))

    def filters(self, table):
        return [q.calls for q in self.queries if q.table == table]


def guest_headers(guest_id=None):
    return {"X-Guest-Session": guest_id or str(uuid.uuid4())}


class GuestIdentity(unittest.TestCase):

    def test_guest_header_gives_a_guest(self):
        gid = str(uuid.uuid4())
        principal = get_user_or_guest(authorization=None, x_guest_session=gid)
        self.assertTrue(is_guest(principal))
        self.assertEqual(principal.guest_id, gid)
        self.assertIsNone(principal.id)

    def test_invalid_guest_id_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            get_user_or_guest(authorization=None, x_guest_session="../../etc/passwd")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_no_identity_is_401(self):
        with self.assertRaises(HTTPException) as ctx:
            get_user_or_guest(authorization=None, x_guest_session=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_bad_token_is_never_downgraded_to_guest(self):
        with mock.patch.object(supabase_auth, "_verify_token", side_effect=HTTPException(401, "expired")):
            with self.assertRaises(HTTPException) as ctx:
                get_user_or_guest(authorization="Bearer bad", x_guest_session=str(uuid.uuid4()))
        self.assertEqual(ctx.exception.status_code, 401)


class GuestEndpoints(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)
        guest_limits.reset()
        self.addCleanup(guest_limits.reset)
        self.addCleanup(main.app.dependency_overrides.clear)

    def test_uploads_and_account_endpoints_still_need_login(self):
        for method, path in [("POST", "/upload/pdf"), ("POST", "/upload/image"), ("POST", "/voice/transcribe"),
                             ("GET", "/profile/me"), ("GET", "/auth/me"), ("POST", "/chat")]:
            with self.subTest(path=path):
                response = self.client.request(method, path, headers=guest_headers())
                self.assertIn(response.status_code, (401, 422))
                self.assertNotEqual(response.status_code, 200)

    def test_guest_conversations_are_filtered_by_guest_id(self):
        gid = str(uuid.uuid4())
        db = FakeSupabase({"conversations": [{"id": "c1", "title": "Hi"}]})
        with mock.patch.object(main, "supabase", db):
            response = self.client.get("/conversations", headers=guest_headers(gid))
        self.assertEqual(response.status_code, 200)
        calls = db.filters("conversations")[0]
        self.assertIn(("eq", ("guest_session_id", gid)), calls)
        self.assertIn(("is_", ("user_id", "null")), calls)
        self.assertFalse(any(c[0] == "eq" and c[1][0] == "user_id" for c in calls))

    def test_guest_cannot_attach_files(self):
        response = self.client.post("/chat/stream", headers=guest_headers(),
                                    json={"query": "summarise", "attachments": [str(uuid.uuid4())]})
        self.assertEqual(response.status_code, 401)
        self.assertIn("log in", response.json()["detail"].lower())

    def test_guest_chat_streams_and_is_saved_without_a_user(self):
        gid = str(uuid.uuid4())
        saved = []
        get_conversation = mock.Mock(return_value={"id": "c1"})
        with mock.patch.multiple(
            main,
            _get_or_create_conversation=get_conversation,
            _get_conversation_history=mock.Mock(return_value=[]),
            _save_message=mock.Mock(side_effect=lambda *a, **k: saved.append(a) or "m1"),
            _set_message_route=mock.Mock(),
            _update_conversation_title_if_needed=mock.Mock(),
            route_query=mock.Mock(return_value=SimpleNamespace(route="DIRECT", reason="general")),
            stream_response=mock.Mock(return_value=iter(["Hello", " guest"])),
        ):
            response = self.client.post("/chat/stream", headers=guest_headers(gid), json={"query": "hi"})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual("".join(e["content"] for e in events if e["type"] == "content"), "Hello guest")
        owner = get_conversation.call_args.args[1]
        self.assertTrue(is_guest(owner))
        self.assertEqual(owner.guest_id, gid)
        self.assertEqual([a[2] for a in saved], ["USER", "ASSISTANT"])

    def test_guest_message_limit(self):
        with mock.patch.object(settings, "GUEST_MESSAGES_PER_HOUR", 2), \
                mock.patch.multiple(
                    main,
                    _get_or_create_conversation=mock.Mock(return_value={"id": "c1"}),
                    _get_conversation_history=mock.Mock(return_value=[]),
                    _save_message=mock.Mock(return_value="m1"),
                    _set_message_route=mock.Mock(),
                    _update_conversation_title_if_needed=mock.Mock(),
                    route_query=mock.Mock(return_value=SimpleNamespace(route="DIRECT", reason="")),
                    stream_response=mock.Mock(side_effect=lambda **k: iter(["ok"])),
                ):
            headers = guest_headers()
            codes = [self.client.post("/chat/stream", headers=headers, json={"query": "hi"}).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    def test_guest_cannot_open_another_guests_or_users_conversation(self):
        db = FakeSupabase({"conversations": []})                         # no owned row matches
        with mock.patch.object(main, "supabase", db):
            response = self.client.get(f"/conversations/{uuid.uuid4()}/messages", headers=guest_headers())
        self.assertEqual(response.status_code, 404)


class ClaimGuestChats(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.addCleanup(main.app.dependency_overrides.clear)
        self.user = make_user()
        self.gid = str(uuid.uuid4())

    def test_requires_a_signed_in_user(self):
        response = self.client.post("/conversations/claim-guest", headers=guest_headers(self.gid),
                                    json={"guest_session_id": self.gid})
        self.assertEqual(response.status_code, 401)

    def test_moves_messages_then_conversations_to_the_user(self):
        main.app.dependency_overrides[get_current_user] = lambda: self.user
        db = FakeSupabase({"conversations": [{"id": "c1"}, {"id": "c2"}]})
        with mock.patch.object(main, "supabase", db):
            response = self.client.post("/conversations/claim-guest", json={"guest_session_id": self.gid})
        self.assertEqual(response.json(), {"success": True, "claimed": 2})

        tables = [q.table for q in db.queries]
        self.assertEqual(tables, ["conversations", "messages", "conversations"])   # messages first
        select, messages, conversations = (q.calls for q in db.queries)
        self.assertIn(("eq", ("guest_session_id", self.gid)), select)
        self.assertIn(("is_", ("user_id", "null")), select)
        self.assertIn(("update", ({"user_id": str(self.user.id)},)), messages)
        self.assertIn(("in_", ("conversation_id", ["c1", "c2"])), messages)
        self.assertIn(("is_", ("user_id", "null")), messages)
        self.assertIn(("update", ({"user_id": str(self.user.id), "guest_session_id": None},)), conversations)
        self.assertIn(("is_", ("user_id", "null")), conversations)

    def test_nothing_to_claim(self):
        main.app.dependency_overrides[get_current_user] = lambda: self.user
        db = FakeSupabase({"conversations": []})
        with mock.patch.object(main, "supabase", db):
            response = self.client.post("/conversations/claim-guest", json={"guest_session_id": self.gid})
        self.assertEqual(response.json(), {"success": True, "claimed": 0})
        self.assertEqual(len(db.queries), 1)

    def test_rejects_non_uuid(self):
        main.app.dependency_overrides[get_current_user] = lambda: self.user
        response = self.client.post("/conversations/claim-guest", json={"guest_session_id": "everyone"})
        self.assertEqual(response.status_code, 422)


class Limits(unittest.TestCase):

    def setUp(self):
        guest_limits.reset()
        self.addCleanup(guest_limits.reset)

    def test_counts_per_guest_and_per_ip_and_expire(self):
        with mock.patch.object(settings, "GUEST_MESSAGES_PER_HOUR", 2):
            self.assertTrue(guest_limits.allow("g1", "1.1.1.1", now=0))
            self.assertTrue(guest_limits.allow("g1", "1.1.1.1", now=1))
            self.assertFalse(guest_limits.allow("g1", "2.2.2.2", now=2))      # same guest, new IP
            self.assertFalse(guest_limits.allow("g2", "1.1.1.1", now=3))      # new guest, same IP
            self.assertTrue(guest_limits.allow("g3", "3.3.3.3", now=4))
            self.assertTrue(guest_limits.allow("g1", "4.4.4.4", now=3601))    # window passed

    def test_zero_disables_guest_chat(self):
        with mock.patch.object(settings, "GUEST_MESSAGES_PER_HOUR", 0):
            self.assertFalse(guest_limits.allow("g", "1.1.1.1"))

    def test_client_ip_prefers_cloudflare_header(self):
        request = SimpleNamespace(headers={"cf-connecting-ip": "9.9.9.9", "x-forwarded-for": "1.1.1.1, 2.2.2.2"},
                                  client=SimpleNamespace(host="10.0.0.1"))
        self.assertEqual(guest_limits.client_ip(request), "9.9.9.9")
        request.headers = {"x-forwarded-for": "1.1.1.1, 2.2.2.2"}
        self.assertEqual(guest_limits.client_ip(request), "2.2.2.2")
        request.headers = {}
        self.assertEqual(guest_limits.client_ip(request), "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
