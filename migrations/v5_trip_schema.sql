-- v5_trip_schema.sql — Widen txt_file in trips
-- NAV can return values longer than 50 chars (multiple file references concatenated)
-- Run with: python run_migration.py v5
-- Run on PROD with: python run_migration.py v5 prod

ALTER TABLE trips ALTER COLUMN txt_file NVARCHAR(MAX)
GO
