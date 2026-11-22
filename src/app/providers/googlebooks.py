import logging

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)

base_url = "https://www.googleapis.com/books/v1/volumes"
# Google Books returns at most 40 results per request
MAX_RESULTS = 40
IMAGE_SIZES = ("extraLarge", "large", "medium", "small", "thumbnail")


def handle_error(error):
    """Handle Google Books API errors."""
    details = None
    response = getattr(error, "response", None)
    if response is not None:
        try:
            details = response.json().get("error", {}).get("message")
        except (requests.exceptions.JSONDecodeError, AttributeError):
            details = None
    raise services.ProviderAPIError(Sources.GOOGLEBOOKS.value, error, details)


def api_params(params=None):
    """Return request params, including the optional API key."""
    params = dict(params or {})
    if settings.GOOGLE_BOOKS_API_KEY:
        params["key"] = settings.GOOGLE_BOOKS_API_KEY
    return params


def search(query, page):
    """Search for books on Google Books."""
    cache_key = (
        f"search_{Sources.GOOGLEBOOKS.value}_{MediaTypes.BOOK.value}_{query}_{page}"
    )
    data = cache.get(cache_key)

    if data is None:
        per_page = min(settings.PER_PAGE, MAX_RESULTS)
        params = {
            "q": query,
            "printType": "books",
            "startIndex": (page - 1) * per_page,
            "maxResults": per_page,
        }
        if settings.GOOGLE_BOOKS_LANGUAGE:
            params["langRestrict"] = settings.GOOGLE_BOOKS_LANGUAGE

        try:
            response = services.api_request(
                Sources.GOOGLEBOOKS.value,
                "GET",
                base_url,
                params=api_params(params),
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        results = [
            {
                "media_id": item["id"],
                "source": Sources.GOOGLEBOOKS.value,
                "media_type": MediaTypes.BOOK.value,
                "title": get_title(item.get("volumeInfo", {})),
                "image": get_image_url(item.get("volumeInfo", {})),
            }
            for item in response.get("items", [])
        ]

        data = helpers.format_search_response(
            page,
            per_page,
            response.get("totalItems", 0),
            results,
        )

        cache.set(cache_key, data)

    return data


def book(media_id):
    """Get metadata for a book from Google Books."""
    cache_key = f"{Sources.GOOGLEBOOKS.value}_{MediaTypes.BOOK.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        try:
            response = services.api_request(
                Sources.GOOGLEBOOKS.value,
                "GET",
                f"{base_url}/{media_id}",
                params=api_params(),
            )
        except requests.exceptions.HTTPError as error:
            if error.response is not None and error.response.status_code in (
                requests.codes.not_found,
                requests.codes.service_unavailable,  # returned for unknown ids
            ):
                services.raise_not_found_error(
                    Sources.GOOGLEBOOKS.value,
                    media_id,
                    "book",
                )
            handle_error(error)

        info = response.get("volumeInfo", {})
        pages = info.get("pageCount") or info.get("printedPageCount")

        data = {
            "media_id": response["id"],
            "source": Sources.GOOGLEBOOKS.value,
            "source_url": info.get("canonicalVolumeLink")
            or f"https://books.google.com/books?id={response['id']}",
            "media_type": MediaTypes.BOOK.value,
            "title": get_title(info),
            "max_progress": pages,
            "image": get_image_url(info),
            "synopsis": get_description(info),
            "genres": get_genres(info),
            "score": get_score(info.get("averageRating")),
            "score_count": info.get("ratingsCount", 0),
            "details": {
                "number_of_pages": pages,
                "publish_date": info.get("publishedDate"),
                "authors": info.get("authors"),
                "publisher": info.get("publisher"),
                "isbn": get_isbns(info),
            },
        }

        cache.set(cache_key, data)

    return data


def get_title(info):
    """Return the title, including the subtitle when present."""
    title = info.get("title", "")
    subtitle = info.get("subtitle")
    if subtitle:
        return f"{title}: {subtitle}"
    return title


def get_image_url(info):
    """Return the best available cover image as an https URL."""
    image_links = info.get("imageLinks") or {}
    for size in IMAGE_SIZES:
        url = image_links.get(size)
        if url:
            # Remove the page curl effect and force https
            url = url.replace("&edge=curl", "")
            return url.replace("http://", "https://", 1)
    return settings.IMG_NONE


def get_description(info):
    """Return the description as plain text."""
    description = info.get("description")
    if not description:
        return "No synopsis available."
    soup = BeautifulSoup(description, "html.parser")
    return " ".join(soup.get_text(separator=" ").split())


def get_genres(info):
    """Return unique genres from Google Books categories like 'Fiction / Thrillers'."""
    genres = []
    for category in info.get("categories", []):
        for raw_part in category.split(" / "):
            part = raw_part.strip()
            if part and part not in genres and part.lower() != "general":
                genres.append(part)
    return genres or None


def get_score(rating):
    """Convert the 1-5 Google Books rating to Yamtrack's 0-10 scale."""
    if not rating:
        return None
    return round(float(rating) * 2, 1)


def get_isbns(info):
    """Return the ISBN-10 and ISBN-13 identifiers."""
    isbns = [
        identifier["identifier"]
        for identifier in info.get("industryIdentifiers", [])
        if identifier.get("type") in ("ISBN_10", "ISBN_13")
    ]
    return isbns or None
