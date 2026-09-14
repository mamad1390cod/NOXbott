-- Fix discount_codes table schema
-- Run this with: sqlite3 noxbot.db < fix_discount_schema.sql

-- Create table if doesn't exist
CREATE TABLE IF NOT EXISTS discount_codes (
    id VARCHAR(36) PRIMARY KEY NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    code VARCHAR(50) NOT NULL UNIQUE,
    discount_type VARCHAR(20) NOT NULL,
    discount_value INTEGER NOT NULL,
    max_eligible_amount BIGINT,
    expires_at DATETIME,
    max_uses INTEGER,
    usage_count INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    description VARCHAR(255)
);

-- Create index on code
CREATE UNIQUE INDEX IF NOT EXISTS uq_discount_codes_code ON discount_codes(code);

-- Create index on is_active
CREATE INDEX IF NOT EXISTS ix_discount_codes_is_active ON discount_codes(is_active);

-- Create index on code (regular)
CREATE INDEX IF NOT EXISTS ix_discount_codes_code ON discount_codes(code);

SELECT 'Discount codes table schema fixed!' as result;
