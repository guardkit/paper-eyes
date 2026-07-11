#!/usr/bin/env python3
"""A deterministic, OFFLINE OpenAI-compatible stub for the Paper Eyes demo (Stage 4 verify_e2e).

This is a PLUMBING stub, not a model. It answers ``POST /v1/chat/completions`` with fixed canned
output so ``papereyes watch`` / ``papereyes run`` execute the real pipeline (identify -> convert ->
locate -> splice -> extract -> emit) end-to-end with NO GPU and NO real endpoint — the hermetic
``--stub`` path of ``verify_e2e.sh``. It returns the SAME canned CH2 output for any input: it proves
the wiring, never accuracy. For a real extraction receipt, run ``verify_e2e.sh --live`` against the
configured model endpoint.

Dispatch is on the requested ``model`` id (the three pins from ``pipeline.yaml``):

- ``*docling*``  -> the bulk document conversion: cyclic page DocTags (page 1, then page 2, ...);
- ``*vision*``   -> a per-region re-read: one transcription line;
- otherwise (the extractor) -> the deterministic extraction JSON.

The canned CH2 content is a synthetic persona of the ``uk-ch2`` formpack — synthetic people, no
client-derived values, no rendered form pixels.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Page 1 (claimant) — the NINO fragmented into 9 single-glyph cells (the boxed-grid signature). The
# CH2 markers ("Child Benefit claim", "Form CH2") let the identify pass route to uk-ch2.
PAGE1 = """\
<loc_43><loc_23><loc_164><loc_33>Child Benefit claim
<loc_43><loc_38><loc_258><loc_44>Form CH2 - claim Child Benefit for one or more children.
<loc_43><loc_57><loc_101><loc_64>1 About you
<loc_43><loc_72><loc_57><loc_77>Title
<loc_186><loc_72><loc_199><loc_77>Ms
<loc_43><loc_84><loc_82><loc_89>First names
<loc_186><loc_84><loc_224><loc_89>Brandon
<loc_43><loc_96><loc_78><loc_101>Last name
<loc_186><loc_96><loc_221><loc_102>Thomas
<loc_43><loc_108><loc_84><loc_113>Date of birth
<loc_186><loc_108><loc_233><loc_113>1989-08-25
<loc_43><loc_120><loc_133><loc_125>National Insurance number
<loc_186><loc_120><loc_192><loc_126>B
<loc_210><loc_120><loc_216><loc_126>N
<loc_226><loc_120><loc_232><loc_126>6
<loc_245><loc_120><loc_251><loc_126>0
<loc_266><loc_120><loc_272><loc_126>5
<loc_286><loc_120><loc_292><loc_126>9
<loc_306><loc_120><loc_312><loc_126>9
<loc_325><loc_120><loc_331><loc_126>0
<loc_346><loc_120><loc_352><loc_126>B
<loc_43><loc_163><loc_74><loc_168>Postcode
<loc_186><loc_163><loc_222><loc_168>S13 9ZD
"""

PAGE2 = """\
<loc_43><loc_30><loc_180><loc_38>2 Children you're claiming for
<loc_43><loc_57><loc_82><loc_62>First names
<loc_186><loc_57><loc_224><loc_62>Joel
<loc_43><loc_69><loc_78><loc_74>Surname
<loc_186><loc_69><loc_221><loc_74>Thomas
<loc_43><loc_81><loc_84><loc_86>Date of birth
<loc_186><loc_81><loc_233><loc_86>2019-12-26
<loc_43><loc_160><loc_120><loc_168>3 Higher income
"""

REGION_TEXT = "<loc_10><loc_10><loc_20><loc_20>BN605990B"

EXTRACTION = {
    "claimant": {
        "title": "Ms", "first_names": "Brandon", "last_name": "Thomas",
        "full_name": "Brandon Thomas", "nino": "BN605990B", "date_of_birth": "1989-08-25",
        "address_lines": ["Studio 08, Beverley harbors", "Hollandland"], "postcode": "S13 9ZD",
    },
    "children": [
        {"first_names": "Joel", "last_name": "Thomas", "full_name": "Joel Thomas",
         "date_of_birth": "2019-12-26"},
    ],
}

_CONVERT_PAGES = [PAGE1, PAGE2]


class _State:
    """A single cyclic counter for the sequential convert calls (identify + bulk pass)."""

    convert_calls = 0


def _completion(content: str, model: str) -> dict[str, object]:
    return {
        "id": "stub-cmpl",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep the demo output quiet
        pass

    def _send(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/v1/models"):
            models = ["granite-docling", "granite-vision-4-1-4b", "qwen36-workhorse"]
            self._send({"object": "list", "data": [{"id": m} for m in models]})
            return
        self._send({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send({"error": "bad json"}, status=400)
            return
        model = str(body.get("model", ""))
        if "docling" in model:
            page = _CONVERT_PAGES[_State.convert_calls % len(_CONVERT_PAGES)]
            _State.convert_calls += 1
            self._send(_completion(page, model))
        elif "vision" in model:
            self._send(_completion(REGION_TEXT, model))
        else:
            self._send(_completion(json.dumps(EXTRACTION), model))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Paper Eyes offline OpenAI-compatible stub")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9099)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"openai_stub: listening on http://{args.host}:{args.port}/v1 (offline plumbing stub)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
