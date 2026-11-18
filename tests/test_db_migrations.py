from sqlalchemy import create_engine, inspect, text

from app.database.db import ensure_paid_date_column


def test_existing_invoice_table_gets_paid_date_column_without_recreation():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE invoices (id INTEGER PRIMARY KEY, invoice_number VARCHAR(100))"))

    ensure_paid_date_column(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("invoices")}
    assert {"id", "invoice_number", "paid_date"}.issubset(columns)
