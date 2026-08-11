"""Real-chain coverage for the PartCategory list filters."""

from django.urls import reverse

from InvenTree.unit_test import InvenTreeAPITestCase


class PartCategoryFilterRealChainTest(InvenTreeAPITestCase):
    """Verify source-backed parent, cascade, and depth combinations."""

    fixtures = ['category', 'location']

    def test_tc_0001_unfiltered_categories(self):
        """TC-0001: Unfiltered category list returns all fixture categories."""
        response = self.get(reverse('api-part-category-list'))

        self.assertEqual(len(response.data), 8)

    def test_tc_0002_direct_children_only(self):
        """TC-0002: Non-cascading parent filter returns direct children only."""
        response = self.get(
            reverse('api-part-category-list'),
            {'parent': 1, 'cascade': False},
        )

        self.assertEqual(len(response.data), 3)

    def test_tc_0003_recursive_children(self):
        """TC-0003: Cascading parent filter returns recursive children."""
        response = self.get(
            reverse('api-part-category-list'),
            {'parent': 1, 'cascade': True},
        )

        self.assertEqual(len(response.data), 5)

    def test_tc_0004_root_depth_zero(self):
        """TC-0004: Root-relative depth zero returns top-level categories."""
        response = self.get(
            reverse('api-part-category-list'),
            {'cascade': True, 'depth': 0},
        )

        self.assertEqual(len(response.data), 2)

    def test_tc_0005_parent_depth_zero(self):
        """TC-0005: Parent-relative depth zero returns an empty list."""
        response = self.get(
            reverse('api-part-category-list'),
            {'parent': 1, 'cascade': True, 'depth': 0},
        )

        self.assertEqual(len(response.data), 0)

    def test_tc_0006_parent_depth_two(self):
        """TC-0006: Parent-relative depth two includes the supported subtree."""
        response = self.get(
            reverse('api-part-category-list'),
            {'parent': 1, 'cascade': True, 'depth': 2},
        )

        self.assertEqual(len(response.data), 5)
