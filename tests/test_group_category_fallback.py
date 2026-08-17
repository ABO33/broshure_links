import asyncio
import unittest
from unittest.mock import patch

from backend.grouped_search import make_category_sale_url
from backend.linker import apply_grouped_search_links, apply_price_check_result
from backend.praktis_playwright import _extract_last_product_category


class FakeBreadcrumbLocator:
    def __init__(self, links):
        self.links = links

    async def evaluate_all(self, _script):
        return self.links

    async def all_text_contents(self):
        return []


class FakeProductPage:
    url = "https://praktis.bg/sistema-za-ohlazhdane-hydro-fit-s-pvc-dyuzi-10-m"

    def locator(self, _selector):
        return FakeBreadcrumbLocator(
            [
                {"href": "https://praktis.bg/", "title": "Начало"},
                {"href": "https://praktis.bg/gradina", "title": "Градина"},
                {
                    "href": "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi",
                    "title": "Напояване и поливни системи",
                },
                {
                    "href": "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi/vodna-magla",
                    "title": "Водна мъгла",
                },
            ]
        )


class FakeJsonLdLocator(FakeBreadcrumbLocator):
    def __init__(self, scripts):
        super().__init__([])
        self.scripts = scripts

    async def all_text_contents(self):
        return self.scripts


class FakeJsonLdProductPage:
    url = FakeProductPage.url

    def locator(self, selector):
        if selector == "script[type='application/ld+json']":
            return FakeJsonLdLocator(
                [
                    """{
                      "@context": "https://schema.org",
                      "@type": "BreadcrumbList",
                      "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Градина", "item": "https://praktis.bg/gradina"},
                        {"@type": "ListItem", "position": 2, "name": "Водна мъгла", "item": "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi/vodna-magla"},
                        {"@type": "ListItem", "position": 3, "name": "Продукт", "item": "https://praktis.bg/sistema-za-ohlazhdane-hydro-fit-s-pvc-dyuzi-10-m"}
                      ]
                    }"""
                ]
            )
        return FakeBreadcrumbLocator([])


class GroupCategoryFallbackTests(unittest.TestCase):
    def setUp(self):
        self.parent = {
            "sku": "3503573",
            "box_type": "complex",
            "box": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        self.variant = {
            "sku": "3503574",
            "box_type": "variant",
            "box": {"x": 10, "y": 70, "width": 80, "height": 20},
        }
        self.group = {
            "parent": self.parent,
            "variants": [self.variant],
            "skus": ["3503573", "3503574"],
        }
        self.resolved = {
            "3503573": {
                "sku": "3503573",
                "status": "linked",
                "url": FakeProductPage.url,
                "title": "Система за охлаждане Hydro-Fit 10 m",
            },
            "3503574": {
                "sku": "3503574",
                "status": "linked",
                "url": "https://praktis.bg/sistema-za-ohlazhdane-hydro-fit-15-m",
                "title": "Система за охлаждане Hydro-Fit 15 m",
            },
        }

    def test_category_sale_url_uses_only_on_sale_filter(self):
        self.assertEqual(
            make_category_sale_url(
                "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi/vodna-magla?old=1"
            ),
            "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi/vodna-magla?onSale=3",
        )

    def test_last_breadcrumb_link_is_used_as_product_category(self):
        category = asyncio.run(_extract_last_product_category(FakeProductPage()))

        self.assertEqual(category["title"], "Водна мъгла")
        self.assertEqual(
            category["url"],
            "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi/vodna-magla",
        )

    def test_json_ld_breadcrumb_is_used_when_visible_breadcrumb_is_missing(self):
        category = asyncio.run(_extract_last_product_category(FakeJsonLdProductPage()))

        self.assertEqual(category["title"], "Водна мъгла")
        self.assertEqual(
            category["url"],
            "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi/vodna-magla",
        )

    def test_price_result_keeps_product_category_metadata(self):
        link = {"sku": "3503573", "status": "search", "url": "https://praktis.bg/catalogsearch/result?q=3503573"}
        apply_price_check_result(
            link,
            {
                "status": "linked",
                "url": FakeProductPage.url,
                "title": "Система за охлаждане Hydro-Fit 10 m",
                "category_url": "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi/vodna-magla",
                "category_title": "Водна мъгла",
            },
        )

        self.assertEqual(link["category_title"], "Водна мъгла")
        self.assertTrue(link["category_url"].endswith("/vodna-magla"))

    def test_failed_name_group_uses_parent_category_without_category_validation(self):
        def count_mismatch(requests, _progress_callback=None):
            return {
                url: {
                    "count_status": "ok",
                    "count_mismatch": True,
                    "found_count": 3,
                    "found_skus": [],
                    "missing_skus": [],
                    "extra_skus": [],
                }
                for url in requests
            }

        category_result = {
            "3503573": {
                "sku": "3503573",
                "category_status": "ok",
                "category_url": "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi/vodna-magla",
                "category_title": "Водна мъгла",
            }
        }
        with (
            patch("backend.linker.count_search_results_with_playwright", side_effect=count_mismatch),
            patch("backend.linker.find_product_categories_with_playwright", return_value=category_result) as categories,
            patch("backend.linker.count_search_results") as http_fallback,
        ):
            apply_grouped_search_links([self.group], self.resolved, validate_counts=True)

        result = self.resolved["3503573"]
        self.assertEqual(result["status"], "group_category_review")
        self.assertEqual(
            result["url"],
            "https://praktis.bg/gradina/napoyavane-i-polivni-sistemi/vodna-magla?onSale=3",
        )
        self.assertEqual(result["group_fallback_reason"], "group_count_mismatch")
        categories.assert_called_once()
        http_fallback.assert_not_called()

    def test_valid_name_group_stays_on_grouped_search(self):
        def valid_count(requests, _progress_callback=None):
            return {
                url: {
                    "count_status": "ok",
                    "found_count": 2,
                    "found_skus": ["3503573", "3503574"],
                    "missing_skus": [],
                    "extra_skus": [],
                }
                for url in requests
            }

        with (
            patch("backend.linker.count_search_results_with_playwright", side_effect=valid_count),
            patch("backend.linker.find_product_categories_with_playwright") as categories,
        ):
            apply_grouped_search_links([self.group], self.resolved, validate_counts=True)

        result = self.resolved["3503573"]
        self.assertEqual(result["status"], "grouped_search")
        self.assertIn("/catalogsearch/result?", result["url"])
        categories.assert_not_called()

    def test_unavailable_category_never_returns_old_mismatch_status_or_bad_group_link(self):
        def count_mismatch(requests, _progress_callback=None):
            return {
                url: {
                    "count_status": "ok",
                    "count_mismatch": True,
                    "found_count": 3,
                    "found_skus": [],
                    "missing_skus": [],
                    "extra_skus": [],
                }
                for url in requests
            }

        with (
            patch("backend.linker.count_search_results_with_playwright", side_effect=count_mismatch),
            patch(
                "backend.linker.find_product_categories_with_playwright",
                return_value={
                    "3503573": {
                        "sku": "3503573",
                        "category_status": "blocked",
                        "category_message": "Category lookup was blocked.",
                    }
                },
            ),
        ):
            apply_grouped_search_links([self.group], self.resolved, validate_counts=True)

        result = self.resolved["3503573"]
        self.assertEqual(result["status"], "group_category_review")
        self.assertEqual(result["url"], FakeProductPage.url)
        self.assertNotIn("/catalogsearch/result?", result["url"])


if __name__ == "__main__":
    unittest.main()
