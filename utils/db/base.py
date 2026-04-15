from sqlalchemy.orm import declarative_base

Base = declarative_base()

MYSQL_TABLE_OPTIONS = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def mysql_table_args(*constraints):
    if constraints:
        return (*constraints, MYSQL_TABLE_OPTIONS.copy())
    return MYSQL_TABLE_OPTIONS.copy()
