"""Navigation resolution and active-item highlighting tests."""

from django.test import SimpleTestCase

from apps.core.context_processors import _resolve_nav_item


class NavigationActiveStateTests(SimpleTestCase):
    def test_dashboard_root_is_active_only_on_the_dashboard_stub(self):
        item = {"label": "Dashboard", "url_name": "accounts:dashboard_redirect"}
        # The stub sits at /dashboard/; "/" is the public website, not a portal.
        self.assertTrue(_resolve_nav_item(item, "/dashboard/")["active"])
        self.assertFalse(_resolve_nav_item(item, "/")["active"])
        self.assertFalse(_resolve_nav_item(item, "/students/")["active"])

    def test_parent_path_is_active_on_child_pages(self):
        item = {"label": "Students", "url_name": "students:list"}
        self.assertTrue(_resolve_nav_item(item, "/students/")["active"])
        self.assertTrue(_resolve_nav_item(item, "/students/2/")["active"])
        self.assertFalse(_resolve_nav_item(item, "/staff/")["active"])

    def test_child_is_active_only_on_exact_page(self):
        item = {"label": "Add Student", "url_name": "students:add"}
        self.assertTrue(_resolve_nav_item(item, "/students/add/")["active"])
        self.assertFalse(_resolve_nav_item(item, "/students/")["active"])

    def test_parent_item_is_active_when_a_child_is_active(self):
        item = {
            "label": "Results",
            "url_name": "results:sheet_list",
            "children": [
                {"label": "Sheets", "url_name": "results:sheet_list"},
                {"label": "Grading", "url_name": "results:grading_list"},
            ],
        }
        self.assertTrue(_resolve_nav_item(item, "/results/grading/")["active"])
