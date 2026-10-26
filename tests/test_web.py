import hashlib
import http.client
import io
import json
import socket
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import msgpack
from PIL import Image
from sssekai.crypto.APIManager import encrypt, SEKAI_APIMANAGER_KEYSETS

from mysekai_web.server import Server, Settings

UID = "123456789"
PASSWORD = "482916"
RAW_KEY = f"{UID}_{PASSWORD}"
KEY = hashlib.sha256(RAW_KEY.encode()).hexdigest()
OTHER = hashlib.sha256(f"{UID}_482917".encode()).hexdigest()


def packet(sites=(5, 6, 7, 8), empty=False):
    return {"updatedResources": {"userMysekaiHarvestMaps": [
        {"mysekaiSiteId": sid, "userId": "123456789", "userMysekaiSiteHarvestResourceDrops": [] if empty else [
            {"resourceType": "mysekai_material", "resourceId": 12, "quantity": 2,
             "positionX": 0, "positionZ": 0, "seq": 1},
            {"resourceType": "mysekai_material", "resourceId": 2, "quantity": 3,
             "positionX": 5, "positionZ": 5, "seq": 2},
        ]} for sid in sites
    ]}}


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.temp.name) / "maps.sqlite3", requests_per_minute=1000)
        self.server = Server(("127.0.0.1", 0), self.settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, key=KEY, headers=None):
        all_headers = {"Authorization": "Bearer " + key, "X-User-Id": UID} if key is not None else {}
        all_headers.update(headers or {})
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=20)
        conn.request(method, path, body, all_headers)
        response = conn.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return result

    def upload(self, data=None, key=KEY, **headers):
        data = packet() if data is None else data
        return self.request("/api/upload", "POST", json.dumps(data).encode(), key, headers)

    def test_encrypted_upload_and_all_map_images(self):
        body = encrypt(msgpack.packb(packet(), use_bin_type=True), SEKAI_APIMANAGER_KEYSETS["cn"])
        status, content, _ = self.request("/api/upload", "POST", body, headers={
            "X-Original-Url": "https://test.invalid/api/user/123456789/mysekai?isForceAllReloadOnlyMysekai=True"})
        self.assertEqual(status, 200, content)
        data = json.loads(content)["data"]
        self.assertEqual([s["id"] for s in data["sites"]], [5, 6, 7, 8])
        self.assertEqual(sum(s["diamonds"] for s in data["sites"]), 8)
        for site in data["sites"]:
            status, image, headers = self.request(site["image_url"])
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "image/png")
            with Image.open(io.BytesIO(image)) as parsed:
                self.assertEqual(parsed.size, (1024, 576))

    def test_key_isolation_including_images_and_no_plaintext_key(self):
        self.assertEqual(self.upload()[0], 200)
        self.assertEqual(self.request("/api/maps", key=OTHER)[0], 404)
        self.assertEqual(self.request("/api/maps/5/image", key=OTHER)[0], 404)
        self.assertEqual(self.request("/api/maps/5/image", key=None)[0], 401)
        with sqlite3.connect(self.settings.db_path) as db:
            row = db.execute("SELECT key_hash, metadata FROM snapshots").fetchone()
        self.assertEqual(row[0], KEY)
        self.assertNotIn(PASSWORD, row[1])
        self.assertEqual(self.upload(packet((6,)), key=OTHER)[0], 200)
        self.assertEqual(len(json.loads(self.request("/api/maps")[1])["data"]["sites"]), 4)

    def test_latest_snapshot_is_atomic_and_persistent(self):
        self.assertEqual(self.upload()[0], 200)
        self.assertEqual(self.upload({"updatedResources": {}})[0], 422)
        self.assertEqual(len(json.loads(self.request("/api/maps")[1])["data"]["sites"]), 4)
        with patch("mysekai_web.server.process", side_effect=RuntimeError("test")):
            self.assertEqual(self.upload()[0], 500)
        self.assertEqual(self.request("/api/maps/5/image")[0], 200)
        self.assertEqual(self.upload(packet((6,), empty=True))[0], 200)
        self.assertEqual(self.request("/api/maps/5/image")[0], 404)
        self.assertEqual(self.request("/api/maps/6/image")[0], 200)
        from mysekai_web.storage import Store
        store = Store(self.settings.db_path)
        data = store.metadata(KEY)
        self.assertEqual([s["id"] for s in data["sites"]], [6])
        self.assertEqual(data["sites"][0]["diamonds"], 0)

    def test_uid_mismatch_rejected_without_overwrite(self):
        self.assertEqual(self.upload()[0], 200)
        self.assertEqual(self.upload(**{"X-Original-Url": "https://test.invalid/api/user/999999999/mysekai"})[0], 422)
        data = packet()
        data["updatedResources"]["userMysekaiHarvestMaps"][0]["userId"] = "999999999"
        self.assertEqual(self.upload(data)[0], 422)
        self.assertEqual(self.request("/api/maps/5/image")[0], 200)

    def test_unrelated_user_ids_are_not_map_owners(self):
        data = packet((5,))
        data["updatedResources"]["friends"] = [{"userId": "999999999"}]
        self.assertEqual(self.upload(data)[0], 200)

    def test_invalid_key_and_query_string_are_not_credentials(self):
        for token in [None, "123456789_12345", "123456789_abcdef", "../data_123456", "１２３４５６_123456"]:
            if token and not token.isascii():
                continue  # HTTP header codec rejects this before sending.
            self.assertEqual(self.request("/api/maps", key=token)[0], 401)
        self.assertEqual(self.request("/api/maps?key=" + KEY, key=None)[0], 401)

    def test_invalid_upload_and_size_limits(self):
        for data in [{}, [], {"updatedResources": {"userMysekaiHarvestMaps": []}}]:
            self.assertEqual(self.upload(data)[0], 422)
        self.assertEqual(self.request("/api/upload", "POST", b"not encrypted")[0], 422)
        self.assertEqual(self.upload(**{"X-Region": "unknown"})[0], 400)
        malformed = packet((5,))
        malformed["updatedResources"]["userMysekaiHarvestMaps"][0]["mysekaiSiteId"] = []
        self.assertEqual(self.upload(malformed)[0], 422)
        self.settings.max_upload_bytes = 10
        self.assertEqual(self.upload()[0], 413)
        self.assertEqual(self.request("/api/upload", "POST", b"")[0], 400)
        self.assertEqual(self.request("/upload", "POST", b"x")[0], 404)

    def test_module_and_frontend_do_not_leak_keys(self):
        status, body, headers = self.request("/api/module")
        self.assertEqual(status, 200)
        self.assertIn(KEY.encode(), body)
        self.assertNotIn(RAW_KEY.encode(), body)
        self.assertIn(f"|{UID}|{KEY}|cn".encode(), body)
        self.assertIn(b"binary-body-mode=1", body)
        self.assertNotIn(b"/suite/", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request("/api/module", key=None)[0], 401)
        for path in ["/", "/app.js", "/auth.js", "/upload.js", "/style.css", "/api/config"]:
            status, body, _ = self.request(path, key=None)
            self.assertEqual(status, 200)
            self.assertNotIn(KEY.encode(), body)
        self.assertEqual(self.request("/../../data/maps.sqlite3")[0], 404)

    def test_uid_header_and_legacy_plaintext_are_rejected(self):
        self.assertEqual(self.request("/api/maps", headers={"X-User-Id": ""})[0], 401)
        self.assertEqual(self.request("/api/maps", key=RAW_KEY)[0], 401)
        self.assertEqual(self.upload()[0], 200)
        self.assertEqual(self.request("/api/maps", headers={"X-User-Id": "999999999"})[0], 401)
        self.assertEqual(self.request("/api/maps/5/image", headers={"X-User-Id": "999999999"})[0], 401)
        self.assertEqual(self.upload(**{"X-User-Id": "999999999"})[0], 401)

    def test_existing_hashed_snapshot_remains_accessible(self):
        # Previous versions stored SHA-256(uid_password), the same token now sent by clients.
        from mysekai_web.storage import Store
        store = Store(self.settings.db_path)
        metadata = {"uid": UID, "updated_at": "2026-10-26T00:00:00Z", "sites": []}
        store.save(hashlib.sha256(RAW_KEY.encode()).hexdigest(), UID, b"legacy", metadata, {5: b"legacy-png"})
        self.assertEqual(json.loads(self.request("/api/maps")[1])["data"]["uid"], UID)
        self.assertEqual(self.request("/api/maps/5/image")[1], b"legacy-png")

    def test_invalid_binary_is_archived_exactly_with_reason(self):
        body = b"\x00\xffinvalid-encrypted-body\x80"
        with self.assertLogs("mysekai", level="WARNING") as logs:
            status, response, _ = self.request("/api/upload", "POST", body)
        result = json.loads(response)
        self.assertEqual(status, 422)
        archive_id = result["archive_id"]
        directory = self.settings.db_path.parent / "failed_uploads"
        self.assertEqual((directory / f"{archive_id}.bin").read_bytes(), body)
        metadata = json.loads((directory / f"{archive_id}.json").read_text())
        self.assertEqual(metadata["message"], result["message"])
        self.assertEqual(metadata["uid"], UID)
        self.assertEqual(metadata["region"], "cn")
        self.assertEqual(metadata["received_bytes"], len(body))
        self.assertEqual(metadata["expected_bytes"], len(body))
        self.assertEqual(metadata["sha256"], hashlib.sha256(body).hexdigest())
        self.assertIn("InvalidUpload", metadata["exception_types"])
        self.assertNotIn(KEY, json.dumps(metadata))
        self.assertNotIn(KEY, "\n".join(logs.output))
        self.assertIn(archive_id, "\n".join(logs.output))
        self.assertEqual(self.request("/api/maps")[0], 404)

    def test_failed_database_write_still_archives_and_preserves_last_map(self):
        self.assertEqual(self.upload(packet((5,)))[0], 200)
        original_image = self.request("/api/maps/5/image")[1]
        with patch.object(self.server.store, "save", side_effect=sqlite3.OperationalError("database unavailable")):
            status, response, _ = self.upload(packet((6,)))
        self.assertEqual(status, 500)
        archive_id = json.loads(response)["archive_id"]
        binary = self.settings.db_path.parent / "failed_uploads" / f"{archive_id}.bin"
        self.assertEqual(json.loads(binary.read_bytes()), packet((6,)))
        self.assertEqual(self.request("/api/maps/5/image")[1], original_image)

    def test_archive_failure_is_reported_without_hiding_upload_error(self):
        with patch.object(self.server.store, "archive_failure", side_effect=OSError("disk full")):
            with self.assertLogs("mysekai", level="ERROR") as logs:
                status, response, _ = self.request("/api/upload", "POST", b"bad data")
        self.assertEqual(status, 422)
        self.assertNotIn("archive_id", json.loads(response))
        self.assertIn("Failed to archive", "\n".join(logs.output))

    def test_incomplete_upload_archives_received_bytes(self):
        body = b"partial upload"
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        connection.putrequest("POST", "/api/upload")
        connection.putheader("Authorization", "Bearer " + KEY)
        connection.putheader("X-User-Id", UID)
        connection.putheader("Content-Length", len(body) + 100)
        connection.endheaders(body)
        connection.sock.shutdown(socket.SHUT_WR)
        response = connection.getresponse()
        result = json.loads(response.read())
        self.assertEqual(response.status, 400)
        connection.close()
        directory = self.settings.db_path.parent / "failed_uploads"
        self.assertEqual((directory / (result["archive_id"] + ".bin")).read_bytes(), body)
        metadata = json.loads((directory / (result["archive_id"] + ".json")).read_text())
        self.assertEqual(metadata["expected_bytes"], len(body) + 100)

    def test_success_and_unauthenticated_requests_do_not_create_failure_archives(self):
        self.assertEqual(self.upload(packet((5,)))[0], 200)
        self.assertEqual(self.request("/api/upload", "POST", b"bad", key=None)[0], 401)
        self.assertFalse((self.settings.db_path.parent / "failed_uploads").exists())

    def test_rate_limit(self):
        self.server.limiter.limit = 2
        self.assertEqual(self.request("/api/maps", key=None)[0], 401)
        self.assertEqual(self.request("/api/maps", key=None)[0], 401)
        self.assertEqual(self.request("/api/maps", key=None)[0], 429)


if __name__ == "__main__":
    unittest.main()
