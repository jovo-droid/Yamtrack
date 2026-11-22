from unittest.mock import MagicMock, patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from app.models import MediaTypes, Sources
from app.providers import googlebooks, services

VOLUME = {
    "id": "abc123XYZ",
    "volumeInfo": {
        "title": "Meeressarg",
        "subtitle": "Kriminalroman",
        "authors": ["Stefan Ahnhem"],
        "publisher": "Ullstein",
        "publishedDate": "2019-04-26",
        "description": "<p>Ein <b>Fall</b> für Fabian Risk.</p>",
        "industryIdentifiers": [
            {"type": "ISBN_13", "identifier": "9783548291451"},
            {"type": "ISBN_10", "identifier": "3548291457"},
            {"type": "OTHER", "identifier": "XYZ"},
        ],
        "pageCount": 560,
        "categories": ["Fiction / Thrillers / General", "Fiction / Crime"],
        "averageRating": 4.5,
        "ratingsCount": 12,
        "imageLinks": {
            "thumbnail": "http://books.google.com/books/content?id=abc&img=1&zoom=1&edge=curl",
        },
        "canonicalVolumeLink": "https://books.google.com/books/about/Meeressarg.html?id=abc123XYZ",
    },
}


@override_settings(GOOGLE_BOOKS_API_KEY="", GOOGLE_BOOKS_LANGUAGE="")
class GoogleBooksTests(TestCase):
    """Tests for the Google Books provider."""

    def setUp(self):
        """Clear the cache before each test."""
        cache.clear()

    @patch("app.providers.googlebooks.services.api_request")
    def test_search(self, mock_api_request):
        """Search results are mapped to Yamtrack's format."""
        mock_api_request.return_value = {"totalItems": 1, "items": [VOLUME]}

        response = googlebooks.search("Meeressarg", 2)

        params = mock_api_request.call_args.kwargs["params"]
        self.assertEqual(params["q"], "Meeressarg")
        self.assertEqual(params["startIndex"], settings.PER_PAGE)
        self.assertNotIn("key", params)
        self.assertNotIn("langRestrict", params)
        self.assertEqual(response["total_results"], 1)
        result = response["results"][0]
        self.assertEqual(result["media_id"], "abc123XYZ")
        self.assertEqual(result["source"], Sources.GOOGLEBOOKS.value)
        self.assertEqual(result["media_type"], MediaTypes.BOOK.value)
        self.assertEqual(result["title"], "Meeressarg: Kriminalroman")
        self.assertEqual(
            result["image"],
            "https://books.google.com/books/content?id=abc&img=1&zoom=1",
        )

    @override_settings(GOOGLE_BOOKS_API_KEY="secret", GOOGLE_BOOKS_LANGUAGE="de")
    @patch("app.providers.googlebooks.services.api_request")
    def test_search_key_and_language(self, mock_api_request):
        """API key and language restriction are sent when configured."""
        mock_api_request.return_value = {"totalItems": 0}

        response = googlebooks.search("Meeressarg", 1)

        params = mock_api_request.call_args.kwargs["params"]
        self.assertEqual(params["key"], "secret")
        self.assertEqual(params["langRestrict"], "de")
        self.assertEqual(response["results"], [])

    @patch("app.providers.googlebooks.services.api_request")
    def test_book(self, mock_api_request):
        """Book metadata is mapped to Yamtrack's format."""
        mock_api_request.return_value = VOLUME

        response = googlebooks.book("abc123XYZ")

        self.assertEqual(response["title"], "Meeressarg: Kriminalroman")
        self.assertEqual(response["max_progress"], 560)
        self.assertEqual(response["synopsis"], "Ein Fall für Fabian Risk.")
        self.assertEqual(response["genres"], ["Fiction", "Thrillers", "Crime"])
        self.assertEqual(response["score"], 9.0)
        self.assertEqual(response["score_count"], 12)
        self.assertEqual(response["details"]["authors"], ["Stefan Ahnhem"])
        self.assertEqual(
            response["details"]["isbn"],
            ["9783548291451", "3548291457"],
        )
        self.assertTrue(response["source_url"].startswith("https://books.google"))

    @patch("app.providers.googlebooks.services.api_request")
    def test_book_minimal(self, mock_api_request):
        """Missing optional fields fall back to defaults."""
        mock_api_request.return_value = {"id": "min", "volumeInfo": {"title": "X"}}

        response = googlebooks.book("min")

        self.assertEqual(response["image"], settings.IMG_NONE)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertIsNone(response["genres"])
        self.assertIsNone(response["score"])
        self.assertIsNone(response["details"]["isbn"])

    @patch("app.providers.googlebooks.services.api_request")
    def test_book_not_found(self, mock_api_request):
        """Unknown volume ids raise a 404 provider error."""
        error_response = MagicMock(status_code=requests.codes.not_found)
        mock_api_request.side_effect = requests.exceptions.HTTPError(
            response=error_response,
        )

        with self.assertRaises(services.ProviderAPIError) as context:
            googlebooks.book("missing")
        self.assertEqual(context.exception.status_code, requests.codes.not_found)

    @patch("app.providers.googlebooks.book")
    def test_services_routes_to_googlebooks(self, mock_book):
        """services.get_media_metadata dispatches Google Books items."""
        mock_book.return_value = {"title": "X"}
        services.get_media_metadata(
            MediaTypes.BOOK.value,
            "abc",
            Sources.GOOGLEBOOKS.value,
        )
        mock_book.assert_called_once_with("abc")
