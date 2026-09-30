import os
import unittest
from unittest.mock import Mock, patch

from app.core.db.mysql_connection_policy import (
    create_mysql_engine,
    mysql_connect_args_for_url,
    should_disable_mysql_ssl,
)


class MySQLConnectionPolicyTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {"MYSQL_SSL_DISABLED_ENDPOINTS": "47.118.50.65:3306/xinhui_test"},
        clear=False,
    )
    def test_ssl_disable_policy_matches_only_exact_endpoint(self):
        self.assertTrue(should_disable_mysql_ssl("47.118.50.65", 3306, "xinhui_test"))
        self.assertFalse(should_disable_mysql_ssl("47.118.50.65", 3307, "xinhui_test"))
        self.assertFalse(should_disable_mysql_ssl("47.118.50.66", 3306, "xinhui_test"))
        self.assertFalse(should_disable_mysql_ssl("47.118.50.65", 3306, "another_db"))

    def test_ssl_disable_policy_is_off_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(should_disable_mysql_ssl("47.118.50.65", 3306, "xinhui_test"))

    @patch.dict(
        os.environ,
        {"MYSQL_SSL_DISABLED_ENDPOINTS": "47.118.50.65:3306/xinhui_test"},
        clear=False,
    )
    def test_connect_args_adds_ssl_disabled_only_for_matching_url(self):
        matching = mysql_connect_args_for_url(
            "mysql+pymysql://user:password@47.118.50.65:3306/xinhui_test",
            base={"connect_timeout": 5},
        )
        other_database = mysql_connect_args_for_url(
            "mysql+pymysql://user:password@47.118.50.65:3306/another_db",
            base={"connect_timeout": 5},
        )

        self.assertEqual(matching, {"connect_timeout": 5, "ssl_disabled": True})
        self.assertEqual(other_database, {"connect_timeout": 5})

    @patch.dict(
        os.environ,
        {"MYSQL_SSL_DISABLED_ENDPOINTS": "47.118.50.65:3306/xinhui_test"},
        clear=False,
    )
    @patch("app.core.db.mysql_connection_policy.sqlalchemy_create_engine")
    def test_engine_factory_merges_existing_connect_args(self, create_engine_mock: Mock):
        create_mysql_engine(
            "mysql+pymysql://user:password@47.118.50.65:3306/xinhui_test",
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )

        create_engine_mock.assert_called_once_with(
            "mysql+pymysql://user:password@47.118.50.65:3306/xinhui_test",
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5, "ssl_disabled": True},
        )


if __name__ == "__main__":
    unittest.main()
