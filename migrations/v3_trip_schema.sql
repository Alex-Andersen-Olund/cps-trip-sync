-- v3_trip_schema.sql — Widen remaining text columns that can contain concatenated values
-- Run with: python run_migration.py v3
-- Run on PROD with: python run_migration.py v3 prod

ALTER TABLE trips ALTER COLUMN txt_product NVARCHAR(MAX)
GO
ALTER TABLE trips ALTER COLUMN start_city  NVARCHAR(MAX)
GO
ALTER TABLE trips ALTER COLUMN end_city    NVARCHAR(MAX)
GO
