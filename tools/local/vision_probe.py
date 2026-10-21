# -*- coding: utf-8 -*-
"""Vision probe for the Strata Ornith-NVFP4 service on 8090.

Sends a real PNG (solid red) through the OpenAI image_url path and prints
whether the encoder answered or the service refused the picture.
"""
import base64
import json
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

KEY = "sk-llama-d5c7afa0e6f1a9f423364ee5b87e9c28e960342c6ba0d979a5d0b620079fae50"
URL = "http://127.0.0.1:8090/v1/chat/completions"


def png_solid(w: int, h: int, rgb: tuple) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    row = b"\x00" + bytes(rgb) * w
    raw = row * h
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    b64 = base64.b64encode(png_solid(64, 64, (255, 0, 0))).decode()
    body = {
        "model": "Ornith-1.5-35B-NVFP4",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图是什么颜色？只回答颜色。"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                ],
            }
        ],
        "max_tokens": 48,
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=300)
        d = json.loads(r.read())
        msg = d["choices"][0]["message"]
        print("VISION HTTP", r.status, f"{time.time() - t0:.1f}s")
        print("content:", repr(msg.get("content"))[:300])
        print("reasoning:", repr(msg.get("reasoning_content"))[:200])
        print("finish:", d["choices"][0].get("finish_reason"))
        print("usage:", d.get("usage"))
    except urllib.error.HTTPError as e:
        print("VISION HTTP", e.code)
        print("body:", e.read().decode("utf-8", "replace")[:600])
    except Exception as e:
        print("VISION FAIL", type(e).__name__, e)


if __name__ == "__main__":
    sys.exit(main())