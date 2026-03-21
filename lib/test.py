"""This file handles the test logic for the lib part of the project."""

import pytest


# This class keeps the view test mixin data and behavior in one place.
class ViewTestMixin(object):
    """
    Automatically load in a session and client, this is common for a lot of
    tests that work with views.
    """

    @pytest.fixture(autouse=True)
    def set_common_fixtures(self, session, client):
        self.session = session
        self.client = client
