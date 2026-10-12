
import httpx

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Pinterest Wallpaper API",
    description="Real Pinterest image search powered by your configured provider.",
    version="1.0.0"
)

UPSTREAM_URL = "https://anshapi.pages.dev/api/pintrest"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def extract_pins(payload):
    """Support the nested response structure returned by the provider."""
    current = payload

    for _ in range(5):
        if isinstance(current, list):
            return current

        if not isinstance(current, dict):
            return []

        if isinstance(current.get("pins"), list):
            return current["pins"]

        if "data" in current:
            current = current["data"]
        else:
            break

    return []


def normalize_pin(pin):
    images = pin.get("images") or {}

    # Prefer the original resolution, then high-resolution alternatives.
    image_url = None
    selected_image = None

    for size in ("orig", "736x", "474x", "236x", "170x"):
        candidate = images.get(size)

        if isinstance(candidate, dict) and candidate.get("url"):
            image_url = candidate["url"]
            selected_image = candidate
            break

    if not image_url:
        return None

    # Only return URLs from the expected Pinterest image host.
    from urllib.parse import urlparse

    parsed = urlparse(image_url)
    if parsed.scheme != "https" or parsed.hostname != "i.pinimg.com":
        return None

    pin_url = pin.get("url") or ""
    pin_parsed = urlparse(pin_url)

    if (
        pin_parsed.scheme != "https"
        or pin_parsed.hostname not in {
            "pinterest.com",
            "www.pinterest.com"
        }
    ):
        pin_url = (
            f"https://www.pinterest.com/pin/{pin['id']}/"
            if pin.get("id") else ""
        )

    return {
        "id": pin.get("id"),
        "title": pin.get("title") or "",
        "description": pin.get("description") or "",
        "image_url": image_url,
        "thumbnail_url": (
            images.get("236x", {}).get("url")
            if isinstance(images.get("236x"), dict)
            else image_url
        ),
        "pin_url": pin_url,
        "alt_text": pin.get("alt_text") or "",
        "width": selected_image.get("width"),
        "height": selected_image.get("height"),
        "dominant_color": pin.get("dominant_color"),
        "author": pin.get("author", {}).get("full_name"),
        "source": "Pinterest"
    }


@app.get("/")
async def home():
    return {
        "name": "Pinterest Wallpaper API",
        "status": "online",
        "endpoints": {
            "search": "/wallpapers?search=radha&images=50",
            "docs": "/docs"
        }
    }


@app.get("/wallpapers")
async def search_wallpapers(
    search: str = Query(..., min_length=1, max_length=150),
    images: int = Query(50, ge=1, le=100)
):
    params = {
        "search": search,
        "images": images
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                UPSTREAM_URL,
                params=params,
                follow_redirects=True
            )
            response.raise_for_status()
            payload = response.json()

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Pinterest image provider timed out."
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Image provider returned HTTP {exc.response.status_code}."
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="Unable to connect to the image provider."
        )
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Image provider returned invalid JSON."
        )

    if not isinstance(payload, dict) or payload.get("status") is False:
        raise HTTPException(
            status_code=502,
            detail="Image provider reported an unsuccessful search."
        )

    pins = extract_pins(payload)

    results = []
    seen = set()

    for pin in pins:
        if not isinstance(pin, dict):
            continue

        item = normalize_pin(pin)

        if not item:
            continue

        unique_key = item["id"] or item["image_url"]

        if unique_key in seen:
            continue

        seen.add(unique_key)
        results.append(item)

        if len(results) >= images:
            break

    return {
        "success": True,
        "query": search,
        "count": len(results),
        "requested_images": images,
        "source": "Pinterest",
        "results": results
    }
