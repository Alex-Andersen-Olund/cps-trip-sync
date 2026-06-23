-- v1_trip_schema.sql — Initial trip schema for cps-trip-sync
-- Run with: python run_migration.py v1
-- Run on PROD with: python run_migration.py v1 prod

-- ------------------------------------------------------------------ --
-- trips
-- ------------------------------------------------------------------ --
CREATE TABLE trips (
    trip_no                  NVARCHAR(20)   NOT NULL,
    line_no                  INT            NOT NULL,
    partial_trip             INT,
    starting_date            DATETIME2,
    start_time               NVARCHAR(10),
    ending_date              DATETIME2,
    end_time                 NVARCHAR(10),
    start_country            NVARCHAR(10),
    start_city               NVARCHAR(100),
    end_country              NVARCHAR(10),
    end_city                 NVARCHAR(100),
    department               NVARCHAR(20),
    plan_department          NVARCHAR(20),
    subdepartment            NVARCHAR(20),
    -- Resource assignments (written by CPS, synced up to NAV)
    vehicle                  NVARCHAR(20),
    trailer                  NVARCHAR(20),
    driver                   NVARCHAR(20),
    driver_2                 NVARCHAR(20),
    -- Status and plan notes
    status                   NVARCHAR(5),
    plan_info                NVARCHAR(MAX),
    plan_info_2              NVARCHAR(MAX),
    txt_product              NVARCHAR(100),
    txt_file                 NVARCHAR(50),
    eupl                     NVARCHAR(20),
    additional_resources     BIT            DEFAULT 0,
    actual_starting_date     DATETIME2,
    actual_ending_date       DATETIME2,
    expected_end_date        DATETIME2,
    -- Sync metadata
    company                  NVARCHAR(50),
    nav_updated_at           DATETIME2,     -- last time TripData_Down wrote this row
    cps_updated_at           DATETIME2,     -- last time CPS wrote a resource assignment
    synced_up_at             DATETIME2,     -- last time TripData_Up pushed this to NAV
    updated_at               DATETIME2      DEFAULT GETUTCDATE(),
    CONSTRAINT PK_trips PRIMARY KEY (trip_no, line_no)
)
GO

CREATE INDEX IX_trips_starting_date ON trips(starting_date)
GO
CREATE INDEX IX_trips_vehicle       ON trips(vehicle)
GO
CREATE INDEX IX_trips_status        ON trips(status)
GO
CREATE INDEX IX_trips_department    ON trips(plan_department)
GO

-- ------------------------------------------------------------------ --
-- routes
-- ------------------------------------------------------------------ --
CREATE TABLE routes (
    trip_no           NVARCHAR(20)   NOT NULL,
    line_no           INT            NOT NULL,
    sequence_no       INT            NOT NULL,
    action_code       NVARCHAR(5),
    address_code      NVARCHAR(20),
    address_name      NVARCHAR(200),
    address           NVARCHAR(200),
    city              NVARCHAR(100),
    country           NVARCHAR(10),
    post_code         NVARCHAR(20),
    starting_date     DATETIME2,
    starting_time     NVARCHAR(10),
    eta_date          DATETIME2,
    eta_time          NVARCHAR(10),
    action_duration   DECIMAL(6,2),
    drive_duration    DECIMAL(6,2),
    decimal_latitude  DECIMAL(12,8),
    decimal_longitude DECIMAL(12,8),
    distance          DECIMAL(10,2),
    status            NVARCHAR(20),
    updated_at        DATETIME2      DEFAULT GETUTCDATE(),
    CONSTRAINT PK_routes PRIMARY KEY (trip_no, line_no, sequence_no)
)
GO

-- ------------------------------------------------------------------ --
-- trip_list
-- ------------------------------------------------------------------ --
CREATE TABLE trip_list (
    trip_no              NVARCHAR(20)   NOT NULL,
    partial_trip_line_no INT            NOT NULL,
    line_no              INT            NOT NULL,
    sequence_no          INT            NOT NULL,
    action_code          NVARCHAR(5),
    address_name         NVARCHAR(200),
    city                 NVARCHAR(100),
    file_no              NVARCHAR(50),
    shipment_no          INT,
    quantity             DECIMAL(10,2),
    unit_of_measure      NVARCHAR(20),
    updated_at           DATETIME2      DEFAULT GETUTCDATE(),
    CONSTRAINT PK_trip_list PRIMARY KEY (trip_no, partial_trip_line_no, line_no, sequence_no)
)
GO

-- ------------------------------------------------------------------ --
-- trip_additional_resources  (Trailer2, Dolly)
-- ------------------------------------------------------------------ --
CREATE TABLE trip_additional_resources (
    line_no       INT            NOT NULL,
    trip_no       NVARCHAR(20)   NOT NULL,
    pt_line_no    INT            NOT NULL,
    resource_type NVARCHAR(20),
    resource_no   NVARCHAR(20),
    updated_at    DATETIME2      DEFAULT GETUTCDATE(),
    CONSTRAINT PK_trip_additional_resources PRIMARY KEY (line_no)
)
GO

-- ------------------------------------------------------------------ --
-- trips_pending_sync  (Up-sync queue)
-- ------------------------------------------------------------------ --
CREATE TABLE trips_pending_sync (
    id               INT            IDENTITY PRIMARY KEY,
    trip_no          NVARCHAR(20)   NOT NULL,
    line_no          INT            NOT NULL,
    company          NVARCHAR(50),
    -- JSON object: { "Vehicle": "1234", "Driver": "56789", "AdditionalResources": [...] }
    changed_fields   NVARCHAR(MAX)  NOT NULL,
    created_at       DATETIME2      DEFAULT GETUTCDATE(),
    attempts         INT            DEFAULT 0,
    last_attempt_at  DATETIME2,
    status           NVARCHAR(20)   DEFAULT 'pending'   -- pending / done / failed
)
GO

CREATE INDEX IX_trips_pending_sync_status ON trips_pending_sync(status, created_at)
GO
