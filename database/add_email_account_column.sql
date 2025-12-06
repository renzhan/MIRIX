-- Quick fix: Add email_account column to users table
-- This script fixes the error: column users.email_account does not exist

-- Create a function to check if a column exists (if not already exists)
CREATE OR REPLACE FUNCTION column_exists(tbl_name text, col_name text) 
RETURNS boolean AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_schema = 'public' 
        AND table_name = tbl_name 
        AND column_name = col_name
    );
END;
$$ LANGUAGE plpgsql;

-- Add email_account column to users table if it doesn't exist
DO $$
BEGIN
    IF NOT column_exists('users', 'email_account') THEN
        ALTER TABLE users ADD COLUMN email_account VARCHAR;
        RAISE NOTICE '✅ Added email_account column to users table';
    ELSE
        RAISE NOTICE 'ℹ️  email_account column already exists in users table';
    END IF;
END;
$$;

-- Clean up
DROP FUNCTION IF EXISTS column_exists(text, text);

-- Final message
DO $$
BEGIN
    RAISE NOTICE '✅ Migration completed!';
END;
$$;

