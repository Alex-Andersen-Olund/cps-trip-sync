-- v2_trip_schema.sql — Column width fixes
-- Run with: python run_migration.py v2
-- Run on PROD with: python run_migration.py v2 prod

ALTER TABLE trips ALTER COLUMN txt_file NVARCHAR(MAX)
GO

-- Also widen other fields that could exceed initial estimates
ALTER TABLE trips ALTER COLUMN plan_info   NVARCHAR(MAX)
GO
ALTER TABLE trips ALTER COLUMN plan_info_2 NVARCHAR(MAX)
GO
ALTER TABLE trips ALTER COLUMN eupl        NVARCHAR(100)
GO
