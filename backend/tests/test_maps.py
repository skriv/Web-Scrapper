import asyncio
import importlib.util
import json
from pathlib import Path

from fastapi import HTTPException
import httpx
import pytest

from app.maps import MapsError, extract_maps, normalize_maps


# Exercise the real private adapter, not a copied validation implementation.
spec = importlib.util.spec_from_file_location("maps_adapter", Path(__file__).parents[2] / "maps" / "adapter.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
URL = "https://www.google.com/maps/place/Kipriakon/data=!4m2!3m1!1s0x14e732fd76f0d90d:0xe5415928d6702b47"
ENTRY = {
    "title": "Kipriakon", "address": "Old port, Limassol 3042", "phone": "25 101555",
    "web_site": "https://example.com", "timezone": "Asia/Nicosia",
    "open_hours": {"Sunday": ["Closed"], "Monday": ["12:30–10 pm"]},
    "complete_address": {"country": "CY"}, "description": "Cypriot restaurant",
    "data_id": "0x14e732fd76f0d90d:0xe5415928d6702b47",
    "place_id": "ChIJDdnwdv0y5xQRRytw1ihZQeU", "cid": "16519582940102929223",
    "credit_cards_accepted": ["Mastercard"],
    "about": [
        {"id": "payments", "options": [{"name": "Cash", "enabled": True}, {"name": "Cheque", "enabled": False}]},
        {"id": "service_options", "options": [{"name": "Delivery", "enabled": True}, {"name": "Drive-through", "enabled": False}]},
    ],
}


def test_normalize_actual_gosom_shape():
    result = normalize_maps(ENTRY, URL)
    assert result.profile.businessName == "Kipriakon"
    assert result.profile.phone == "+35725101555"
    assert result.profile.timezone == "Asia/Nicosia"
    assert result.profile.openingHours == ["Monday: 12:30–10 pm", "Sunday: Closed"]
    assert result.profile.paymentMethods == ["Mastercard", "Cash"]
    assert result.profile.services == ["Delivery"]
    assert result.provenance["phone"] == [{"url": URL, "method": "gosom"}]


def test_missing_maps_fields_stay_empty_and_invalid_values_removed():
    result = normalize_maps({"title": "A", "timezone": "wrong-zone", "web_site": "javascript:alert(1)"}, URL)
    assert result.profile.timezone == result.profile.website == result.profile.address == ""
    assert result.profile.services == result.profile.paymentMethods == []
    assert len(result.warnings) >= 2
    assert normalize_maps({"title": "A", "web_site": "https://[broken"}, URL).profile.website == ""


@pytest.mark.parametrize("url", [
    "https://evil.example/maps/place/name", "https://www.google.com.evil.com/maps/place/name",
    "https://www.google.com/maps/search/dentists", "http://www.google.com/maps/place/name",
    "https://www.google.com:8080/maps/place/name", "https://user@www.google.com/maps/place/name",
    "https://www.google.com/maps/place/name\nhttps://evil.com", "https://www.google.com/maps?cid=garbage",
])
def test_adapter_rejects_searches_and_untrusted_urls(url):
    with pytest.raises(HTTPException) as error:
        adapter.validate_place_url(url)
    assert error.value.status_code == 422


def test_adapter_preserves_maps_payload_and_accepts_identified_links():
    assert adapter.validate_place_url(URL) == URL
    assert adapter.validate_place_url("https://www.google.com/maps?cid=16519582940102929223")
    assert adapter.validate_place_url("https://www.google.co.uk/maps/place/A")


def test_ndjson_single_place_and_identity():
    assert adapter.parse_results(json.dumps(ENTRY) + "\n", URL)["title"] == "Kipriakon"
    assert adapter.parse_results(json.dumps([ENTRY]), URL)["title"] == "Kipriakon"
    with pytest.raises(HTTPException) as error:
        adapter.parse_results(json.dumps(ENTRY) + "\n" + json.dumps(ENTRY), URL)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        adapter.parse_results(json.dumps({**ENTRY, "data_id": "other"}), URL)
    assert error.value.status_code == 502
    with pytest.raises(HTTPException):
        adapter.verify_identity(ENTRY, "https://www.google.com/maps?cid=123")


@pytest.mark.parametrize("data,status", [("", 404), ("not-json", 502), ('{"phone":"1"}', 502)])
def test_adapter_empty_or_broken_output(data, status):
    with pytest.raises(HTTPException) as error:
        adapter.parse_results(data, URL)
    assert error.value.status_code == status


def test_maps_client_success_and_error(monkeypatch):
    real_client = httpx.AsyncClient
    async def handle(request):
        assert request.url.path == "/extract"
        assert json.loads(request.content)["url"] == URL + "?hl=en&gl=us"
        return httpx.Response(200, json={"entry": ENTRY})
    monkeypatch.setattr("app.maps.httpx.AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle)))
    assert asyncio.run(extract_maps(URL)).profile.businessName == "Kipriakon"
    async def failure(request):
        return httpx.Response(504, json={"detail": "Maps timeout"})
    monkeypatch.setattr("app.maps.httpx.AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(failure)))
    with pytest.raises(MapsError, match="Maps timeout"):
        asyncio.run(extract_maps(URL))


def test_adapter_timeout_kills_process_and_cleans_temp(monkeypatch):
    killed, temp_folders = [], []
    class Process:
        returncode = None
        pid = 123456
        async def wait(self):
            self.returncode = -9
    async def create(*args, **kwargs):
        assert "-json" in args and kwargs["start_new_session"] is True
        # Upstream's inactivity clock starts at zero and cancels the first job
        # before its initial Maps navigation finishes. Our outer timeout owns it.
        assert "-exit-on-inactivity" not in args
        assert kwargs["env"]["DISABLE_TELEMETRY"] == "1"
        temp_folders.append(Path(kwargs["cwd"]))
        return Process()
    monkeypatch.setattr(adapter.shutil, "which", lambda binary: "/bin/gosom")
    monkeypatch.setattr(adapter.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(adapter, "TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(adapter, "kill_group", lambda process: killed.append(process.pid))
    with pytest.raises(HTTPException) as error:
        asyncio.run(adapter.run_scraper(URL))
    assert error.value.status_code == 504
    assert killed == [123456]
    assert temp_folders and not temp_folders[0].exists()


def test_adapter_success_uses_cli_output_and_cleans_process(monkeypatch):
    killed = []
    class Process:
        returncode = 0
        pid = 654321
        async def wait(self):
            return 0
    async def create(*args, **kwargs):
        output = Path(args[args.index("-results") + 1])
        output.write_text(json.dumps(ENTRY) + "\n")
        return Process()
    monkeypatch.setattr(adapter.shutil, "which", lambda binary: "/bin/gosom")
    monkeypatch.setattr(adapter.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(adapter, "kill_group", lambda process: killed.append(process.pid))
    assert asyncio.run(adapter.run_scraper(URL))["title"] == "Kipriakon"
    assert killed == [654321]
