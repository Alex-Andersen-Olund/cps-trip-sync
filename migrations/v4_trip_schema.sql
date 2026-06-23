-- v4_trip_schema.sql — Widen action_code in routes and trip_list
-- NAV can return values longer than 5 chars (e.g. 'AFHAD-XX')
-- Run with: python run_migration.py v4
-- Run on PROD with: python run_migration.py v4 prod

ALTER TABLE routes    ALTER COLUMN action_code NVARCHAR(20)
GO
ALTER TABLE trip_list ALTER COLUMN action_code NVARCHAR(20)
GO
